#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Service backup module for service-oriented Docker backup system.
Manages backup process for a specific service.
"""

import os
import json
import time
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Union, Tuple

from logger import get_logger
from database_backup import DatabaseBackup
from file_backup import FileBackup
from utils.docker_utils import get_container_environment
from utils.archive_utils import create_tar_gz

logger = get_logger(__name__)


class ServiceBackup:
    """Manages backup process for a specific service."""
    
    def __init__(self, service_name: str, containers: List[Any], config: Dict[str, Any]):
        """
        Initialize service backup handler.
        
        Args:
            service_name (str): Name of the service.
            containers (list): List of container objects.
            config (dict): Service configuration.
        """
        self.service_name = service_name
        self.containers = containers
        self.config = config
        
        # Get service-specific configuration
        self.db_config = config.get('database', {})
        self.files_config = config.get('files', {})
        self.global_config = config.get('global', {})
        
        # Identify database and application containers
        self.db_containers = self._identify_db_containers()
        self.app_containers = self._identify_app_containers()
        
        logger.info(f"Initialized service backup for {service_name} with "
                  f"{len(self.db_containers)} database and {len(self.app_containers)} "
                  f"application containers")
    
    def backup(self) -> bool:
        """
        Execute full service backup.
        
        Returns:
            bool: True if successful, False otherwise.
        """
        if self.global_config.get('exclude_from_backup', False):
            logger.info(f"Service {self.service_name} is excluded from backup")
            return False
        
        # Generate timestamp for backup
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join("/backups", f"{self.service_name}_{timestamp}")
        
        logger.info(f"Starting backup of service {self.service_name} to {backup_path}")
        
        # Create temporary staging directory
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                # Determine if containers need to be stopped
                requires_stopping = (self.db_config.get('requires_stopping', False) or 
                                    self.files_config.get('requires_stopping', False))
                
                # Stop containers if needed
                stopped_containers = []
                if requires_stopping:
                    stopped_containers = self._stop_containers()
                
                try:
                    # Back up databases
                    db_success = self._backup_databases(os.path.join(temp_dir, "databases"))
                    
                    # Back up application data
                    app_success = self._backup_app_data(os.path.join(temp_dir, "files"))
                    
                    # Create metadata
                    metadata_success = self._create_metadata(temp_dir, timestamp)
                    
                    # Check if any part was successful
                    if not (db_success or app_success):
                        logger.error(f"Backup failed for service {self.service_name}: "
                                   f"No databases or files backed up successfully")
                        return False
                    
                    # Create final archive
                    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                    logger.info(f"Creating final archive for service {self.service_name}")
                    
                    archive_path = f"{backup_path}.tar.gz"
                    if create_tar_gz(temp_dir, archive_path):
                        logger.info(f"Successfully created backup archive: {archive_path}")
                        return True
                    else:
                        logger.error(f"Failed to create backup archive for {self.service_name}")
                        return False
                        
                finally:
                    # Always restart stopped containers
                    self._start_containers(stopped_containers)
                
            except Exception as e:
                logger.error(f"Error during service backup: {str(e)}")
                return False
    
    def _identify_db_containers(self) -> List[Any]:
        """
        Identify database containers in service.
        
        Returns:
            list: List of database container objects.
        """
        db_containers = []
        db_patterns = self.db_config.get('container_patterns', [])
        
        for container in self.containers:
            # Check if container looks like a database
            is_db = False
            
            # First check by image name
            if hasattr(container, 'image') and container.image.tags:
                image_name = container.image.tags[0].lower()
                is_db = any(db_type in image_name for db_type in 
                           ["postgres", "mysql", "mariadb", "mongo", "redis", "sqlite"])
            
            # Then check against configured patterns
            if not is_db and db_patterns:
                container_name = container.name.lower()
                for pattern in db_patterns:
                    # Convert wildcard pattern to Python compatible
                    py_pattern = pattern.replace('*', '').lower()
                    if py_pattern in container_name:
                        is_db = True
                        break
            
            if is_db:
                db_containers.append(container)
                logger.debug(f"Identified database container: {container.name}")
        
        return db_containers
    
    def _identify_app_containers(self) -> List[Any]:
        """
        Identify application containers in service.
        
        Returns:
            list: List of application container objects.
        """
        # All containers that aren't database containers are considered app containers
        app_containers = []
        
        for container in self.containers:
            if container not in self.db_containers:
                app_containers.append(container)
                logger.debug(f"Identified application container: {container.name}")
        
        return app_containers
    
    def _stop_containers(self) -> List[Any]:
        """
        Stop containers for consistent backup.
        Avoids stopping the backup container itself.
        Improves container identification with multiple methods.
        
        Returns:
            list: List of stopped container objects.
        """
        logger.info(f"Stopping containers for service {self.service_name}")
        stopped_containers = []
        
        try:
            # Get the current container's identifiers
            current_identifiers = self._get_current_container_identifiers()
            logger.debug(f"Current container identifiers: {current_identifiers}")
            
            # Identify critical infrastructure containers that should not be stopped
            critical_patterns = ['flann', 'registry', 'network', 'proxy']
            critical_containers = []
            
            for container in self.containers:
                is_critical = False
                if hasattr(container, 'name'):
                    container_name = container.name.lower()
                    for pattern in critical_patterns:
                        if pattern in container_name:
                            logger.info(f"Container {container.name} identified as critical infrastructure, will not stop")
                            is_critical = True
                            break
                
                if is_critical:
                    critical_containers.append(container)
            
            # Stop containers in reverse order (dependencies first)
            for container in reversed(self.containers):
                # Skip current container
                if self._is_current_container(container, current_identifiers):
                    logger.info(f"Skipping current container: {container.name}")
                    continue
                    
                # Skip critical containers
                if container in critical_containers:
                    continue
                    
                if hasattr(container, 'status') and container.status == "running":
                    logger.debug(f"Stopping container: {container.name}")
                    try:
                        container.stop(timeout=30)  # Give containers 30 seconds to stop
                        stopped_containers.append(container)
                    except Exception as e:
                        logger.error(f"Error stopping container {container.name}: {str(e)}")
            
            # Short delay to ensure containers are fully stopped
            if stopped_containers:
                time.sleep(2)
            
            return stopped_containers
                
        except Exception as e:
            logger.error(f"Error stopping containers: {str(e)}")
            # Try to restart any containers that were stopped
            self._start_containers(stopped_containers)
            return []
    
    def _start_containers(self, containers: List[Any]) -> None:
        """
        Start containers after backup with validation and health checking.
        
        Args:
            containers (list): List of container objects to start.
        """
        if not containers:
            return
        
        logger.info(f"Starting containers for service {self.service_name}")
        
        # Create a list to track successfully started containers
        started_containers = []
        failed_containers = []
        
        # Start containers in original order
        for container in containers:
            try:
                if not hasattr(container, 'name'):
                    logger.warning(f"Container object missing name attribute, skipping")
                    continue
                    
                logger.debug(f"Starting container: {container.name}")
                container.start()
                
                # Verify container started successfully
                start_time = time.time()
                max_wait = 30  # Maximum seconds to wait for container to start
                
                while time.time() - start_time < max_wait:
                    # Refresh container status
                    try:
                        container.reload()
                        if container.status == "running":
                            started_containers.append(container)
                            logger.debug(f"Container {container.name} started successfully")
                            break
                    except Exception as e:
                        logger.warning(f"Error checking container status: {str(e)}")
                    
                    # Wait a bit before checking again
                    time.sleep(1)
                
                # If container didn't start within timeout
                if container not in started_containers:
                    logger.error(f"Container {container.name} failed to start within {max_wait} seconds")
                    failed_containers.append(container)
                    
            except Exception as e:
                logger.error(f"Error starting container {container.name}: {str(e)}")
                failed_containers.append(container)
        
        # Log summary
        if started_containers:
            logger.info(f"Successfully started {len(started_containers)} containers")
        
        if failed_containers:
            logger.error(f"Failed to start {len(failed_containers)} containers: " + 
                        ", ".join(c.name for c in failed_containers if hasattr(c, 'name')))

    def _get_current_container_identifiers(self) -> Dict[str, str]:
        """
        Get identifiers for the current container.
        
        Returns:
            dict: Dictionary with hostname, container ID, and name.
        """
        identifiers = {}
        
        # Get hostname
        try:
            import socket
            identifiers['hostname'] = socket.gethostname()
        except Exception as e:
            logger.debug(f"Could not determine hostname: {str(e)}")
        
        # Get container ID
        try:
            with open('/proc/self/cgroup', 'r') as f:
                for line in f:
                    if 'docker' in line:
                        identifiers['container_id'] = line.split('/')[-1].strip()
                        break
        except Exception as e:
            logger.debug(f"Could not determine container ID: {str(e)}")
        
        # Get environment variables that might indicate container name
        try:
            identifiers['container_name'] = os.environ.get('HOSTNAME', '')
        except Exception:
            pass
            
        return identifiers

    def _is_current_container(self, container: Any, current_identifiers: Dict[str, str]) -> bool:
        """
        Check if a container is the current container.
        
        Args:
            container: Container object to check.
            current_identifiers: Dictionary of current container identifiers.
            
        Returns:
            bool: True if this is the current container, False otherwise.
        """
        # If container is missing required attributes, assume it's not the current container
        if not hasattr(container, 'id') or not hasattr(container, 'name'):
            return False
        
        # Check by container ID (most reliable)
        if 'container_id' in current_identifiers and current_identifiers['container_id']:
            if container.id == current_identifiers['container_id']:
                return True
        
        # Check by hostname
        if 'hostname' in current_identifiers and current_identifiers['hostname']:
            if container.name == current_identifiers['hostname']:
                return True
            
        # Check by environment variable name
        if 'container_name' in current_identifiers and current_identifiers['container_name']:
            if container.name == current_identifiers['container_name']:
                return True
        
        # Check by service name
        backup_service_names = os.environ.get('BACKUP_SERVICE_NAMES', 'container-backup,backup')
        backup_names = [name.strip() for name in backup_service_names.split(',')]
        
        for name in backup_names:
            if container.name == name or container.name.startswith(f"{name}_"):
                return True
        
        return False

    def _backup_databases(self, backup_dir: str) -> bool:
        """
        Back up all databases in service.
        
        Args:
            backup_dir (str): Directory to store backups.
            
        Returns:
            bool: True if any database was backed up successfully, False otherwise.
        """
        if not self.db_containers:
            logger.info(f"No database containers found for service {self.service_name}")
            return False
        
        os.makedirs(backup_dir, exist_ok=True)
        success_count = 0
        
        for container in self.db_containers:
            try:
                logger.info(f"Backing up database in container: {container.name}")
                
                # Get environment variables for credential extraction
                env_vars = get_container_environment(container)
                
                # Set up database backup handler
                db_backup = DatabaseBackup(
                    container=container,
                    db_type=self.db_config.get('type'),
                    config=self.db_config
                )
                
                # Extract credentials
                if self.db_config.get('credentials'):
                    # Use configured credentials
                    credentials = self.db_config.get('credentials')
                else:
                    # Extract credentials from environment
                    credentials = db_backup.get_credentials_from_environment(
                        env_vars, self.service_name)
                
                # Set credentials
                db_backup.credentials = credentials
                
                # Execute backup
                backup_path = os.path.join(backup_dir, f"{container.name}.sql.gz")
                if db_backup.backup(backup_path):
                    success_count += 1
                
            except Exception as e:
                logger.error(f"Error backing up database container {container.name}: {str(e)}")
        
        return success_count > 0
    
    def _backup_app_data(self, backup_dir: str) -> bool:
        """
        Back up all application data in service.
        
        Args:
            backup_dir (str): Directory to store backups.
            
        Returns:
            bool: True if any application data was backed up successfully, False otherwise.
        """
        if not self.app_containers:
            logger.info(f"No application containers found for service {self.service_name}")
            return False
        
        os.makedirs(backup_dir, exist_ok=True)
        success_count = 0
        
        # Get configured paths and exclusions
        paths = self.files_config.get('data_paths', [])
        exclusions = self.files_config.get('exclusions', [])
        
        for container in self.app_containers:
            try:
                logger.info(f"Backing up data in container: {container.name}")
                
                # Set up file backup handler
                file_backup = FileBackup(
                    container=container,
                    paths=paths,
                    exclusions=exclusions
                )
                
                # Execute backup
                backup_path = os.path.join(backup_dir, f"{container.name}.tar.gz")
                if file_backup.backup(backup_path):
                    success_count += 1
                
            except Exception as e:
                logger.error(f"Error backing up application container {container.name}: {str(e)}")
        
        return success_count > 0
    
    def _create_archive(self, backup_dir: str, timestamp: str) -> Optional[str]:
        """
        Create final archive of service backup.
        
        Args:
            backup_dir (str): Directory containing backups.
            timestamp (str): Timestamp for archive name.
            
        Returns:
            str or None: Path to created archive, or None if failed.
        """
        archive_path = os.path.join("/backups", f"{self.service_name}_{timestamp}.tar.gz")
        
        try:
            # Create the archive
            if create_tar_gz(backup_dir, archive_path):
                logger.info(f"Created backup archive: {archive_path}")
                return archive_path
            else:
                logger.error(f"Failed to create backup archive")
                return None
                
        except Exception as e:
            logger.error(f"Error creating backup archive: {str(e)}")
            return None
    
    def _create_metadata(self, backup_dir: str, timestamp: str) -> bool:
        """
        Create metadata file for backup.
        
        Args:
            backup_dir (str): Directory to store metadata.
            timestamp (str): Backup timestamp.
            
        Returns:
            bool: True if successful, False otherwise.
        """
        metadata = {
            "service_name": self.service_name,
            "timestamp": timestamp,
            "created_at": datetime.now().isoformat(),
            "containers": [],
            "config": {
                "database": self.db_config,
                "files": self.files_config,
                "global": self.global_config
            }
        }
        
        # Add container information
        for container in self.containers:
            container_info = {
                "name": container.name,
                "id": container.id,
                "image": container.image.tags[0] if container.image.tags else "unknown",
                "status": container.status,
                "type": "database" if container in self.db_containers else "application"
            }
            metadata["containers"].append(container_info)
        
        try:
            # Write metadata to file
            metadata_path = os.path.join(backup_dir, "metadata.json")
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            logger.debug(f"Created backup metadata at {metadata_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error creating backup metadata: {str(e)}")
            return False
