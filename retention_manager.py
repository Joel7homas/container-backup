#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Retention manager for service-oriented Docker backup system.
Manages backup retention policies and lifecycle of backup archives.
"""

import os
import re
import json
import time
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Union, Tuple, Set

from logger import get_logger

logger = get_logger(__name__)


class RetentionManager:
    """Manages backup retention policies."""
    
    def __init__(self, backup_dir: str, config: Optional[Dict[str, Any]] = None):
        """
        Initialize retention manager.
        
        Args:
            backup_dir (str): Directory containing backups.
            config (dict, optional): Retention configuration.
        """
        self.backup_dir = Path(backup_dir)
        self.config = config or {}
        
        # Default retention settings
        self.default_days = self.config.get('days', 7)
        self.default_count = self.config.get('count', 10)
        self.service_configs = self.config.get('services', {})
        
        # Backup filename pattern
        self.filename_pattern = r'^(.+)_(\d{8}_\d{6})\.tar\.gz$'
        
        # Ensure backup directory exists
        os.makedirs(self.backup_dir, exist_ok=True)
        
        logger.debug(f"Initialized retention manager for {self.backup_dir}")
    
    def apply_policy(self) -> int:
        """
        Apply retention policy to backups.
        
        Returns:
            int: Number of backups removed.
        """
        logger.info("Applying retention policies to backups")
        
        # Check for lock files to avoid removing backups in use
        active_backups = self._get_active_backups()
        if active_backups:
            logger.info(f"Found {len(active_backups)} active backups that will be preserved")
        
        # Group backups by service name
        service_backups = self._group_backups_by_service()
        
        removed_count = 0
        
        # Apply retention policy to each service
        for service_name, backups in service_backups.items():
            try:
                service_config = self.service_configs.get(service_name, {})
                
                if 'mixed' in service_config:
                    # Apply mixed retention policy
                    daily = service_config['mixed'].get('daily', 7)
                    weekly = service_config['mixed'].get('weekly', 4)
                    monthly = service_config['mixed'].get('monthly', 3)
                    
                    logger.debug(f"Applying mixed retention policy for {service_name}: "
                               f"daily={daily}, weekly={weekly}, monthly={monthly}")
                    
                    service_removed = self.apply_mixed_retention(
                        service_name, daily, weekly, monthly, active_backups)
                    
                elif 'days' in service_config:
                    # Apply time-based retention
                    days = service_config['days']
                    logger.debug(f"Applying time-based retention for {service_name}: {days} days")
                    service_removed = self.apply_time_based_retention(
                        service_name, days, active_backups)
                    
                elif 'count' in service_config:
                    # Apply count-based retention
                    count = service_config['count']
                    logger.debug(f"Applying count-based retention for {service_name}: {count} backups")
                    service_removed = self.apply_count_based_retention(
                        service_name, count, active_backups)
                    
                else:
                    # Apply default retention (time-based)
                    logger.debug(f"Applying default time-based retention for {service_name}: "
                               f"{self.default_days} days")
                    service_removed = self.apply_time_based_retention(
                        service_name, self.default_days, active_backups)
                
                removed_count += service_removed
                
            except Exception as e:
                logger.error(f"Error applying retention policy for {service_name}: {str(e)}")
        
        logger.info(f"Retention policies applied, removed {removed_count} backups")
        return removed_count
    
    def apply_time_based_retention(self, service_name: str, days: int, 
                                 active_backups: Optional[Set[Path]] = None) -> int:
        """
        Apply time-based retention policy.
        
        Args:
            service_name (str): Service name to apply policy to.
            days (int): Number of days to keep backups.
            active_backups (set, optional): Set of active backup paths to preserve.
            
        Returns:
            int: Number of backups removed.
        """
        if days <= 0:
            logger.warning(f"Invalid retention days ({days}) for {service_name}, skipping")
            return 0
        
        active_backups = active_backups or set()
        cutoff_date = datetime.now() - timedelta(days=days)
        removed_count = 0
        
        # Get all backups for this service
        service_backups = self._get_service_backups(service_name)
        
        for backup_path in service_backups:
            try:
                # Skip active backups
                if backup_path in active_backups:
                    logger.debug(f"Skipping active backup: {backup_path.name}")
                    continue
                
                # Extract timestamp from filename
                match = re.match(self.filename_pattern, backup_path.name)
                if not match:
                    logger.warning(f"Skipping backup with invalid filename: {backup_path.name}")
                    continue
                
                # Parse timestamp
                timestamp_str = match.group(2)  # Format: YYYYMMDD_HHMMSS
                backup_date = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                
                # Check if backup is older than cutoff date
                if backup_date < cutoff_date:
                    logger.info(f"Removing old backup: {backup_path.name}")
                    os.remove(backup_path)
                    removed_count += 1
                
            except Exception as e:
                logger.error(f"Error processing backup {backup_path.name}: {str(e)}")
        
        return removed_count
    
    def apply_count_based_retention(self, service_name: str, count: int,
                                  active_backups: Optional[Set[Path]] = None) -> int:
        """
        Apply count-based retention policy.
        
        Args:
            service_name (str): Service name to apply policy to.
            count (int): Number of backups to keep.
            active_backups (set, optional): Set of active backup paths to preserve.
            
        Returns:
            int: Number of backups removed.
        """
        if count <= 0:
            logger.warning(f"Invalid retention count ({count}) for {service_name}, skipping")
            return 0
        
        active_backups = active_backups or set()
        removed_count = 0
        
        # Get all backups for this service
        service_backups = self._get_service_backups(service_name)
        
        # Sort backups by date (newest first)
        sorted_backups = sorted(
            service_backups,
            key=lambda p: self._get_backup_timestamp(p.name),
            reverse=True
        )
        
        # Keep the newest 'count' backups, remove the rest
        if len(sorted_backups) > count:
            backups_to_remove = sorted_backups[count:]
            
            for backup_path in backups_to_remove:
                try:
                    # Skip active backups
                    if backup_path in active_backups:
                        logger.debug(f"Skipping active backup: {backup_path.name}")
                        continue
                    
                    logger.info(f"Removing excess backup: {backup_path.name}")
                    os.remove(backup_path)
                    removed_count += 1
                    
                except Exception as e:
                    logger.error(f"Error removing backup {backup_path.name}: {str(e)}")
        
        return removed_count
    
    def apply_mixed_retention(self, service_name: str, daily_count: int, weekly_count: int,
                            monthly_count: int, active_backups: Optional[Set[Path]] = None) -> int:
        """
        Apply mixed retention policy.
        
        Args:
            service_name (str): Service name to apply policy to.
            daily_count (int): Number of daily backups to keep.
            weekly_count (int): Number of weekly backups to keep.
            monthly_count (int): Number of monthly backups to keep.
            active_backups (set, optional): Set of active backup paths to preserve.
            
        Returns:
            int: Number of backups removed.
        """
        active_backups = active_backups or set()
        removed_count = 0
        
        # Get all backups for this service
        service_backups = self._get_service_backups(service_name)
        
        # Group backups by day, week, and month
        daily_backups = {}
        weekly_backups = {}
        monthly_backups = {}
        
        for backup_path in service_backups:
            timestamp = self._get_backup_timestamp(backup_path.name)
            if not timestamp:
                continue
            
            # Group by day
            day_key = timestamp.strftime("%Y-%m-%d")
            if day_key not in daily_backups:
                daily_backups[day_key] = []
            daily_backups[day_key].append(backup_path)
            
            # Group by week
            week_key = f"{timestamp.year}-W{timestamp.strftime('%V')}"
            if week_key not in weekly_backups:
                weekly_backups[week_key] = []
            weekly_backups[week_key].append(backup_path)
            
            # Group by month
            month_key = timestamp.strftime("%Y-%m")
            if month_key not in monthly_backups:
                monthly_backups[month_key] = []
            monthly_backups[month_key].append(backup_path)
        
        # Sort groups by date
        sorted_days = sorted(daily_backups.keys(), reverse=True)
        sorted_weeks = sorted(weekly_backups.keys(), reverse=True)
        sorted_months = sorted(monthly_backups.keys(), reverse=True)
        
        # For each day, keep the newest backup for that day
        to_keep = set()
        for day in sorted_days[:daily_count]:
            newest = sorted(daily_backups[day], 
                          key=lambda p: self._get_backup_timestamp(p.name),
                          reverse=True)[0]
            to_keep.add(newest)
        
        # For each week, keep the newest backup for that week
        for week in sorted_weeks[:weekly_count]:
            newest = sorted(weekly_backups[week],
                          key=lambda p: self._get_backup_timestamp(p.name),
                          reverse=True)[0]
            to_keep.add(newest)
        
        # For each month, keep the newest backup for that month
        for month in sorted_months[:monthly_count]:
            newest = sorted(monthly_backups[month],
                          key=lambda p: self._get_backup_timestamp(p.name),
                          reverse=True)[0]
            to_keep.add(newest)
        
        # Remove backups not in the keep set
        for backup_path in service_backups:
            if backup_path not in to_keep and backup_path not in active_backups:
                try:
                    logger.info(f"Removing backup under mixed policy: {backup_path.name}")
                    os.remove(backup_path)
                    removed_count += 1
                except Exception as e:
                    logger.error(f"Error removing backup {backup_path.name}: {str(e)}")
        
        return removed_count
    
    def _get_service_backups(self, service_name: str) -> List[Path]:
        """
        Get all backups for a specific service.
        
        Args:
            service_name (str): Service name.
            
        Returns:
            list: List of backup paths.
        """
        backups = []
        pattern = f"{service_name}_*.tar.gz"
        
        for backup_path in self.backup_dir.glob(pattern):
            if backup_path.is_file():
                backups.append(backup_path)
        
        return backups
    
    def _get_backup_timestamp(self, filename: str) -> Optional[datetime]:
        """
        Extract timestamp from backup filename.
        
        Args:
            filename (str): Backup filename.
            
        Returns:
            datetime or None: Backup timestamp or None if invalid.
        """
        match = re.match(self.filename_pattern, filename)
        if not match:
            return None
        
        try:
            timestamp_str = match.group(2)  # Format: YYYYMMDD_HHMMSS
            return datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
        except Exception:
            return None
    
    def _group_backups_by_service(self) -> Dict[str, List[Path]]:
        """
        Group backup files by service name.
        
        Returns:
            dict: Dictionary of service names to backup lists.
        """
        service_backups = {}
        
        for backup_path in self.backup_dir.glob("*.tar.gz"):
            match = re.match(self.filename_pattern, backup_path.name)
            if match:
                service_name = match.group(1)
                if service_name not in service_backups:
                    service_backups[service_name] = []
                service_backups[service_name].append(backup_path)
        
        return service_backups
    
    def _get_active_backups(self) -> Set[Path]:
        """
        Get set of backups that are currently in use.
        
        Returns:
            set: Set of active backup paths.
        """
        active_backups = set()
        
        # Check for lock files
        for lock_file in self.backup_dir.glob("*.lock"):
            try:
                with open(lock_file, 'r') as f:
                    backup_name = f.read().strip()
                    backup_path = self.backup_dir / backup_name
                    if backup_path.exists():
                        active_backups.add(backup_path)
            except Exception as e:
                logger.error(f"Error reading lock file {lock_file}: {str(e)}")
        
        return active_backups
