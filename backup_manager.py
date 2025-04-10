#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Backup manager for service-oriented Docker backup system.
Orchestrates the backup process across all services.
"""

import os
import json
import time
import threading
import concurrent.futures
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Union, Tuple, Set

from logger import get_logger
from service_discovery import ServiceDiscovery
from service_backup import ServiceBackup
from retention_manager import RetentionManager

logger = get_logger(__name__)


class BackupManager:
    """Orchestrates the backup process across all services."""
    
    def __init__(self, portainer_client: Any, config_manager: Any):
        """
        Initialize backup manager.
        
        Args:
            portainer_client (PortainerClient): Portainer client instance.
            config_manager (ConfigurationManager): Configuration manager instance.
        """
        self.portainer_client = portainer_client
        self.config_manager = config_manager
        
        # Default configuration
        self.backup_dir = Path(os.environ.get('BACKUP_DIR', '/backups'))
        self.max_workers = int(os.environ.get('MAX_CONCURRENT_BACKUPS', '3'))
        self.retention_days = int(os.environ.get('BACKUP_RETENTION_DAYS', '7'))
        self.lock_dir = self.backup_dir / 'locks'
        
        # Service discovery
        self.service_discovery = ServiceDiscovery(portainer_client, config_manager)
        
        # Initialize retention manager
        self.retention_config = {
            'days': self.retention_days,
            'services': {}  # Will be populated from service configs
        }
        self.retention_manager = RetentionManager(self.backup_dir, self.retention_config)
        
        # Create backup directory if it doesn't exist
        os.makedirs(self.backup_dir, exist_ok=True)
        os.makedirs(self.lock_dir, exist_ok=True)
        
        logger.info(f"Initialized backup manager (max workers: {self.max_workers})")
    
    def run_backups(self, service_names: Optional[List[str]] = None) -> Dict[str, bool]:
        """
        Run backups for all services or specific services.
        
        Args:
            service_names (list, optional): List of service names to back up.
                                          If None, back up all services.
            
        Returns:
            dict: Dictionary of service names to backup results.
        """
        start_time = time.time()
        logger.info("Starting backup process")
        
        # Discover services
        services = self.service_discovery.discover_services()
        
        # Filter services if service_names provided
        if service_names:
            services = [s for s in services if s.service_name in service_names]
            logger.info(f"Filtered to {len(services)} specified services")
        
        if not services:
            logger.warning("No services found to back up")
            return {}
        
        logger.info(f"Found {len(services)} services to back up")
        
        # Sort services by priority
        services.sort(key=lambda s: s.config.get('global', {}).get('priority', 50))
        
        # Update retention configuration from service configs
        self._update_retention_config(services)
        
        # Run backups in parallel with limited concurrency
        results = {}
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit backup tasks
            future_to_service = {
                executor.submit(self._run_backup_with_lock, service): service
                for service in services
            }
            
            # Process results as they complete
            for future in concurrent.futures.as_completed(future_to_service):
                service = future_to_service[future]
                try:
                    success = future.result()
                    results[service.service_name] = success
                    if success:
                        logger.info(f"Backup completed successfully for {service.service_name}")
                    else:
                        logger.error(f"Backup failed for {service.service_name}")
                except Exception as e:
                    logger.error(f"Exception during backup of {service.service_name}: {str(e)}")
                    results[service.service_name] = False
        
        # Apply retention policies
        deleted_count = self.apply_retention_policy()
        
        elapsed_time = time.time() - start_time
        success_count = sum(1 for result in results.values() if result)
        
        logger.info(f"Backup process completed in {elapsed_time:.1f}s - "
                  f"{success_count}/{len(services)} successful, "
                  f"{deleted_count} backups removed by retention policy")
        
        return results
    
    def run_backup_for_service(self, service_name: str) -> bool:
        """
        Run backup for a specific service.
        
        Args:
            service_name (str): Name of the service.
            
        Returns:
            bool: True if successful, False otherwise.
        """
        logger.info(f"Starting backup for service: {service_name}")
        
        # Discover services
        services = self.service_discovery.discover_services()
        
        # Find the requested service
        service = next((s for s in services if s.service_name == service_name), None)
        
        if not service:
            logger.error(f"Service not found: {service_name}")
            return False
        
        # Create lock file
        lock_path = self._create_lock(service_name)
        if not lock_path:
            logger.error(f"Could not create lock for service: {service_name}")
            return False
        
        try:
            # Run backup
            success = service.backup()
            return success
        except Exception as e:
            logger.error(f"Error during backup of {service_name}: {str(e)}")
            return False
        finally:
            # Always remove lock file
            self._remove_lock(lock_path)
    
    def apply_retention_policy(self) -> int:
        """
        Apply retention policy to backup archives.
        
        Returns:
            int: Number of archives deleted.
        """
        logger.info("Applying retention policies to backups")
        try:
            return self.retention_manager.apply_policy()
        except Exception as e:
            logger.error(f"Error applying retention policies: {str(e)}")
            return 0
    
    def get_backup_status(self) -> Dict[str, Any]:
        """
        Get status of all backups.
        
        Returns:
            dict: Dictionary of backup statuses.
        """
        logger.info("Getting backup status")
        status = {
            'timestamp': datetime.now().isoformat(),
            'backup_directory': str(self.backup_dir),
            'services': {},
            'retention_config': self.retention_config,
            'active_backups': [],
            'storage': {
                'total_size': 0,
                'backup_count': 0
            }
        }
        
        # Get all backup files
        backup_files = list(self.backup_dir.glob("*.tar.gz"))
        status['storage']['backup_count'] = len(backup_files)
        
        # Group backups by service
        services_backups = {}
        
        for backup_file in backup_files:
            # Parse filename to extract service name and timestamp
            parts = backup_file.stem.split('_')
            if len(parts) >= 2:
                service_name = '_'.join(parts[:-2])  # Everything before timestamp
                timestamp = '_'.join(parts[-2:])  # Last two parts form timestamp
                
                if service_name not in services_backups:
                    services_backups[service_name] = []
                
                # Get file size and last modified time
                file_stats = backup_file.stat()
                size_mb = file_stats.st_size / (1024 * 1024)  # Convert to MB
                status['storage']['total_size'] += size_mb
                
                services_backups[service_name].append({
                    'filename': backup_file.name,
                    'timestamp': timestamp,
                    'size_mb': round(size_mb, 2),
                    'last_modified': datetime.fromtimestamp(file_stats.st_mtime).isoformat()
                })
        
        # Sort backups by timestamp (newest first)
        for service_name, backups in services_backups.items():
            backups.sort(key=lambda b: b['timestamp'], reverse=True)
            
            status['services'][service_name] = {
                'backup_count': len(backups),
                'latest_backup': backups[0] if backups else None,
                'total_size_mb': round(sum(b['size_mb'] for b in backups), 2),
                'backups': backups
            }
        
        # Get active backups (with locks)
        for lock_file in self.lock_dir.glob("*.lock"):
            try:
                status['active_backups'].append(lock_file.stem)
            except Exception:
                pass
        
        # Round total size for better readability
        status['storage']['total_size'] = round(status['storage']['total_size'], 2)
        
        return status
    
    def _run_backup_with_lock(self, service: ServiceBackup) -> bool:
        """
        Run backup for a service with proper locking.
        
        Args:
            service (ServiceBackup): Service backup instance.
            
        Returns:
            bool: True if successful, False otherwise.
        """
        service_name = service.service_name
        logger.debug(f"Running backup for service: {service_name}")
        
        # Create lock file
        lock_path = self._create_lock(service_name)
        if not lock_path:
            logger.error(f"Could not create lock for service: {service_name}")
            return False
        
        try:
            # Run backup
            success = service.backup()
            return success
        except Exception as e:
            logger.error(f"Error during backup of {service_name}: {str(e)}")
            return False
        finally:
            # Always remove lock file
            self._remove_lock(lock_path)
    
    def _create_lock(self, service_name: str) -> Optional[Path]:
        """
        Create a lock file for a service.
        
        Args:
            service_name (str): Service name.
            
        Returns:
            Path or None: Path to lock file if created, None otherwise.
        """
        lock_path = self.lock_dir / f"{service_name}.lock"
        
        # Check if lock already exists
        if lock_path.exists():
            # Check if lock is stale (older than 6 hours)
            lock_time = lock_path.stat().st_mtime
            if time.time() - lock_time > 6 * 3600:
                logger.warning(f"Removing stale lock for {service_name}")
                os.remove(lock_path)
            else:
                logger.warning(f"Service {service_name} is already being backed up")
                return None
        
        try:
            # Generate unique backup name (will be filled in at backup time)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{service_name}_{timestamp}.tar.gz"
            
            # Create lock file with backup name
            with open(lock_path, 'w') as f:
                f.write(backup_name)
            
            return lock_path
        except Exception as e:
            logger.error(f"Error creating lock file: {str(e)}")
            return None
    
    def _remove_lock(self, lock_path: Path) -> None:
        """
        Remove a lock file.
        
        Args:
            lock_path (Path): Path to lock file.
        """
        try:
            if lock_path.exists():
                os.remove(lock_path)
        except Exception as e:
            logger.error(f"Error removing lock file: {str(e)}")
    
    def _update_retention_config(self, services: List[ServiceBackup]) -> None:
        """
        Update retention configuration from service configs.
        
        Args:
            services (list): List of service backup instances.
        """
        for service in services:
            service_name = service.service_name
            global_config = service.config.get('global', {})
            
            # Extract retention configuration
            if 'backup_retention' in global_config:
                days = global_config['backup_retention']
                self.retention_config['services'][service_name] = {'days': days}
                logger.debug(f"Using retention days {days} for {service_name}")
            
            # Extract mixed retention if available
            if 'mixed_retention' in global_config:
                mixed = global_config['mixed_retention']
                self.retention_config['services'][service_name] = {
                    'mixed': {
                        'daily': mixed.get('daily', 7),
                        'weekly': mixed.get('weekly', 4),
                        'monthly': mixed.get('monthly', 3)
                    }
                }
                logger.debug(f"Using mixed retention for {service_name}")
        
        # Update retention manager with new config
        self.retention_manager.config = self.retention_config
