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
        Execute full service backup with configurable backup methods.
        
        Returns:
            bool: True if successful, False otherwise.
        """
        logger.info(f"Starting backup for service: {self.service_name}")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        try:
            # Create temporary directory for backup
            with tempfile.TemporaryDirectory() as temp_dir:
                logger.debug(f"Created temporary directory for backup: {temp_dir}")
                success = True
                
                # Determine backup method
                backup_method = os.environ.get('BACKUP_METHOD', 'mounts').lower()
                logger.info(f"Using backup method: {backup_method}")
                
                # Determine if stopping containers is required
                requires_stopping = self.config.get('requires_stopping', False)
                if backup_method == 'container_cp':
                    # Container cp method may require stopping for consistency
                    requires_stopping = True
                
                # Stop containers if required
                stopped_containers = []
                if requires_stopping:
                    stopped_containers = self._stop_containers()
                    if not stopped_containers and len(self.containers) > 0:
                        logger.warning("No containers were stopped, backup may be inconsistent")
                
                try:
                    # Backup databases
                    if self.db_containers:
                        logger.info(f"Backing up {len(self.db_containers)} databases")
                        db_success = self._backup_databases(temp_dir)
                        success = success and db_success
                    
                    # Backup application data based on backup method
                    if backup_method == 'mounts':
                        # Use bind mounts for backup
                        logger.info("Using bind mounts for application data backup")
                        app_success = self._backup_bind_mounts(temp_dir)
                    else:  # container_cp
                        # Use docker cp for backup
                        logger.info("Using docker cp for application data backup")
                        app_success = self._backup_container_data(temp_dir)
                    
                    success = success and app_success
                    
                    # Create final archive if any data was backed up
                    if success and (self.db_containers or backup_method != 'none'):
                        archive_path = self._create_archive(temp_dir, timestamp)
                        if not archive_path:
                            logger.error("Failed to create final archive")
                            success = False
                    
                    return success
                finally:
                    # Always restart containers that were stopped
                    if stopped_containers:
                        self._start_containers(stopped_containers)
                        
        except Exception as e:
            logger.error(f"Error during backup of {self.service_name}: {str(e)}")
            return False

    def _is_system_directory(self, path: str) -> bool:
        """
        Check if a path is a system directory that should be excluded.
        
        Args:
            path (str): Path to check.
            
        Returns:
            bool: True if system directory, False otherwise.
        """
        system_dirs = [
            "/proc", "/sys", "/dev", "/run", "/var/run", 
            "/var/lock", "/tmp", "/var/tmp", "/var/cache",
            "/etc/hostname", "/etc/hosts", "/etc/resolv.conf",
            "/mnt/media", "/media", "/backups", "/mnt/backups"
        ]
        
        return any(path == sys_dir or path.startswith(sys_dir + "/") for sys_dir in system_dirs)

    def _get_unique_bind_mounts(self) -> List[Dict[str, str]]:
        """
        Get a list of unique bind mounts across all containers in the service.
        
        Returns:
            list: List of unique bind mount dictionaries.
        """
        unique_mounts = []
        seen_sources = set()
        
        # Process each container in the service
        for container in self.containers:
            try:
                # Get mounts for this container
                from utils.docker_utils import get_container_mounts
                mounts = get_container_mounts(container)
                
                # Filter for bind mounts
                for mount in mounts:
                    source = mount.get('source', '')
                    
                    # Skip if empty source or already seen
                    if not source or source in seen_sources:
                        continue
                    
                    # Skip system directories
                    if self._is_system_directory(source):
                        continue
                    
                    # Add to unique mounts and track source
                    unique_mounts.append(mount)
                    seen_sources.add(source)
                    
            except Exception as e:
                logger.error(f"Error getting mounts for container {container.name}: {str(e)}")
        
        return unique_mounts

    def _backup_bind_mounts(self, backup_dir: str) -> bool:
        """
        Back up bind mounts with path exclusion support.
        
        Args:
            backup_dir (str): Directory to store backups.
            
        Returns:
            bool: True if successful, False otherwise.
        """
        logger.info(f"Backing up bind mounts for service: {self.service_name}")
        
        # Find all unique bind mounts across containers
        bind_mounts = self._get_unique_bind_mounts()
        if not bind_mounts:
            logger.warning(f"No bind mounts found for service: {self.service_name}")
            return True
        
        logger.debug(f"Found {len(bind_mounts)} unique bind mounts")
        
        # Back up each bind mount
        success = True
        for mount in bind_mounts:
            source = mount.get('Source')
            
            # Skip if source is empty or None
            if not source:
                continue
            
            # Create a FileBackup instance with exclusions
            file_backup = FileBackup(
                container=None,  # Not needed for bind mounts
                paths=[source],
                exclusions=self.config.get('file_exclusions', [])
            )
            
            # Create output path for this mount
            mount_name = os.path.basename(source)
            output_path = os.path.join(backup_dir, f"mount_{mount_name}.tar.gz")
            
            # Backup the mount
            mount_success = file_backup.backup(source, output_path)
            if not mount_success:
                logger.error(f"Failed to back up bind mount: {source}")
                success = False
        
        return success
    
    def _backup_container_data(self, backup_dir: str) -> bool:
        """
        Back up container data using docker cp with improved exclusion and size limits.
        
        Args:
            backup_dir (str): Directory to store backups.
            
        Returns:
            bool: True if successful, False otherwise.
        """
        logger.info(f"Backing up container data for service: {self.service_name}")
        
        # Check disk space before proceeding
        if not self._check_disk_space():
            logger.error("Insufficient disk space for container data backup")
            return False
        
        # Back up each app container
        success = True
        for container in self.app_containers:
            if not hasattr(container, 'name'):
                continue
                
            container_name = container.name
            logger.info(f"Backing up container data for: {container_name}")
            
            # Create output path for this container
            output_path = os.path.join(backup_dir, f"container_{container_name}.tar.gz")
            
            # Create a FileBackup instance with exclusions
            file_backup = FileBackup(
                container=container,
                paths=["/"],  # Back up entire container
                exclusions=self.config.get('file_exclusions', [])
            )
            
            # Set size limit for container backup (default 1GB)
            max_size = int(os.environ.get('MAX_CONTAINER_BACKUP_SIZE', 1024)) * 1024 * 1024
            file_backup.max_size = max_size
            
            # Backup the container
            container_success = file_backup.backup_container(output_path)
            if not container_success:
                logger.error(f"Failed to back up container data for: {container_name}")
                success = False
        
        return success
    
    def _check_disk_space(self) -> bool:
        """
        Check if there's enough disk space for container backup.
        
        Returns:
            bool: True if enough space, False otherwise.
        """
        try:
            # Get backup directory
            backup_dir = os.environ.get('BACKUP_DIR', '/backups')
            
            # Check available space
            disk_stats = os.statvfs(backup_dir)
            available_space = disk_stats.f_frsize * disk_stats.f_bavail
            
            # Get minimum required space (default 5GB)
            required_space = int(os.environ.get('MIN_REQUIRED_SPACE', 5120)) * 1024 * 1024
            
            logger.debug(f"Available space: {available_space / (1024*1024):.2f} MB, " +
                         f"Required: {required_space / (1024*1024):.2f} MB")
            
            return available_space > required_space
        except Exception as e:
            logger.error(f"Error checking disk space: {str(e)}")
            return False  # Default to False if there's an error

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
        Stop containers for consistent backup with improved exclusion handling.
        
        Returns:
            list: List of stopped container objects.
        """
        logger.info(f"Stopping containers for service {self.service_name}")
        stopped_containers = []
        
        try:
            # Get current backup container identifiers
            current_identifiers = self._get_current_container_identifiers()
            logger.debug(f"Current container identifiers: {current_identifiers}")
            
            # Get stack name if available
            stack_name = self._get_stack_name()
            
            # Stop containers in reverse order (dependencies first)
            for container in reversed(self.containers):
                # Skip current container
                if self._is_current_container(container, current_identifiers):
                    logger.info(f"Skipping current container: {container.name}")
                    continue
                
                # Check if container allows hot backup
                hot_backup = self._check_hot_backup_support(container)
                if hot_backup:
                    logger.info(f"Container {container.name} supports hot backup, not stopping")
                    continue
                
                # Stop the container
                if hasattr(container, 'status') and container.status == "running":
                    logger.info(f"Stopping container: {container.name}")
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
    
    def _check_hot_backup_support(self, container) -> bool:
        """
        Check if a container supports hot backup.
        
        Args:
            container: Container object
            
        Returns:
            bool: True if hot backup is supported, False otherwise
        """
        # Default to cold backup
        if not hasattr(container, 'name') or not hasattr(container, 'labels'):
            return False
        
        # Check container labels
        labels = container.labels if isinstance(container.labels, dict) else {}
        
        # Check for explicit hot backup label
        if labels.get('backup.hot', '').lower() == 'true':
            return True
        
        # Check for container type that typically supports hot backup
        image = labels.get('org.opencontainers.image.name', '')
        if not image and hasattr(container, 'image'):
            image = str(container.image)
        
        # Database containers often support hot backup
        db_types = ['postgres', 'mysql', 'mariadb', 'mongodb', 'redis']
        if any(db_type in image.lower() for db_type in db_types):
            return True
        
        # Default to cold backup
        return False
    
    def _start_containers(self, containers: List[Any]) -> None:
        """
        Start containers after backup with validation and health checking.
        Enhanced with better error handling and retry logic.
        
        Args:
            containers (list): List of container objects to start.
        """
        if not containers:
            return
        
        logger.info(f"Starting containers for service {self.service_name}")
        
        # Create lists to track container status
        started_containers = []
        failed_containers = []
        
        # Start containers in reverse order (dependencies last)
        for container in containers:
            try:
                if not hasattr(container, 'name'):
                    logger.warning(f"Container object missing name attribute, skipping")
                    continue
                    
                logger.info(f"Starting container: {container.name}")
                container.start()
                
                # Verify container started successfully
                start_time = time.time()
                max_wait = 60  # Increased timeout for slower services
                
                while time.time() - start_time < max_wait:
                    # Refresh container status
                    try:
                        container.reload()
                        if container.status == "running":
                            # Check for health status if available
                            health = getattr(container, 'health', {})
                            if health and isinstance(health, dict):
                                health_status = health.get('Status', '')
                                if health_status == 'unhealthy':
                                    logger.warning(f"Container {container.name} is running but unhealthy")
                                    time.sleep(2)
                                    continue
                                
                            # Container is running (and healthy if health check exists)
                            started_containers.append(container)
                            logger.info(f"Container {container.name} started successfully")
                            break
                    except Exception as e:
                        logger.warning(f"Error checking container status: {str(e)}")
                    
                    # Wait a bit before checking again
                    time.sleep(2)
                
                # If container didn't start within timeout
                if container not in started_containers:
                    logger.error(f"Container {container.name} failed to start within {max_wait} seconds")
                    # Try another restart with increased timeout
                    try:
                        logger.info(f"Attempting another restart for {container.name}")
                        container.restart(timeout=60)
                        time.sleep(5)
                        container.reload()
                        if container.status == "running":
                            started_containers.append(container)
                            logger.info(f"Container {container.name} started successfully on second attempt")
                        else:
                            failed_containers.append(container)
                    except Exception as e:
                        logger.error(f"Error restarting container {container.name}: {str(e)}")
                        failed_containers.append(container)
                    
            except Exception as e:
                logger.error(f"Error starting container {container.name}: {str(e)}")
                failed_containers.append(container)
        
        # Log summary
        if started_containers:
            logger.info(f"Successfully started {len(started_containers)} containers")
        
        if failed_containers:
            names = [c.name for c in failed_containers if hasattr(c, 'name')]
            logger.error(f"Failed to start {len(failed_containers)} containers: {', '.join(names)}")
    
    def _container_needs_stopping(self, container):
        """
        Determine if a container needs to be stopped for backup.
        
        Args:
            container: Container object
            
        Returns:
            bool: True if container needs stopping, False otherwise
        """
        # If container doesn't have a name, we can't evaluate it properly
        if not hasattr(container, 'name'):
            return False
            
        container_name = container.name.lower()
        
        # Skip containers based on labels or service information if possible
        if hasattr(container, 'labels'):
            labels = container.labels if isinstance(container.labels, dict) else {}
            
            # Check for "hot backup" label
            if labels.get('container-backup.hot', '').lower() == 'true':
                logger.debug(f"Container {container_name} marked for hot backup via label")
                return False
                
            # Check database containers - many support hot backup
            db_images = ['postgres', 'mysql', 'mariadb', 'mongo', 'redis']
            image_name = labels.get('org.opencontainers.image.name', '')
            if any(db in image_name.lower() for db in db_images):
                logger.debug(f"Container {container_name} identified as database, using hot backup")
                return False
        
        # Default: need to stop the container for backup
        return True
    
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
