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
        Run backups for all services or specific services with resource awareness.
        
        Args:
            service_names (list, optional): List of service names to back up.
                                          If None, back up all services.
            
        Returns:
            dict: Dictionary of service names to backup results.
        """
        start_time = time.time()
        logger.info("Starting backup process")
        
        # System resource check before starting
        self._check_system_resources()
        
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
        
        # Filter out backup service itself
        backup_service_names = os.environ.get('BACKUP_SERVICE_NAMES', 'container-backup,backup').split(',')
        backup_service_names = [name.strip() for name in backup_service_names]
        services = [s for s in services if s.service_name not in backup_service_names]
        
        # Sort services by priority
        services.sort(key=lambda s: s.config.get('global', {}).get('priority', 50))
        
        # Update retention configuration from service configs
        self._update_retention_config(services)
        
        # Determine optimal number of workers based on system resources
        max_workers = self._get_optimal_worker_count()
        
        # Run backups in parallel with limited concurrency
        results = {}
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit backup tasks
            future_to_service = {
                executor.submit(self._run_backup_with_lock, service): service
                for service in services
            }
            
            # Process results as they complete
            for future in concurrent.futures.as_completed(future_to_service):
                service = future_to_service[future]
                try:
                    # Check for resource pressure before processing result
                    self._throttle_if_needed()
                    
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
    
    def _check_system_resources(self) -> None:
        """Check system resources and log warnings if resources are low."""
        try:
            import psutil
            
            # Check disk space
            disk_usage = psutil.disk_usage(str(self.backup_dir))
            if disk_usage.percent > 90:
                logger.warning(f"Low disk space: {disk_usage.free / (1024**3):.1f} GB free ({disk_usage.percent}% used)")
            
            # Check CPU load
            cpu_percent = psutil.cpu_percent(interval=1)
            if cpu_percent > 80:
                logger.warning(f"High CPU usage: {cpu_percent}%")
            
            # Check memory usage
            memory = psutil.virtual_memory()
            if memory.percent > 90:
                logger.warning(f"Low memory: {memory.available / (1024**3):.1f} GB available ({memory.percent}% used)")
                
        except ImportError:
            logger.debug("psutil not available, skipping resource check")
        except Exception as e:
            logger.warning(f"Error checking system resources: {str(e)}")
    
    def _get_optimal_worker_count(self) -> int:
        """
        Determine optimal number of worker threads based on system resources.
        
        Returns:
            int: Optimal number of worker threads.
        """
        try:
            import psutil
            
            # Start with configured max_workers
            workers = self.max_workers
            
            # Get CPU count
            cpu_count = psutil.cpu_count(logical=True)
            
            # Get memory info
            memory = psutil.virtual_memory()
            
            # Get disk IO rates
            disk_io = psutil.disk_io_counters(perdisk=False)
            
            # Adjust based on CPU - don't use more than 75% of CPUs
            cpu_workers = max(1, int(cpu_count * 0.75))
            workers = min(workers, cpu_workers)
            
            # Adjust based on memory - reduce workers if memory is tight
            if memory.percent > 80:
                memory_factor = 1 - ((memory.percent - 80) / 20)  # Scale from 1.0 to 0.0
                memory_workers = max(1, int(workers * memory_factor))
                workers = min(workers, memory_workers)
                
            # Always allow at least 1 worker
            return max(1, workers)
        except ImportError:
            logger.debug("psutil not available, using configured max_workers")
            return self.max_workers
        except Exception as e:
            logger.warning(f"Error determining optimal worker count: {str(e)}")
            return self.max_workers
    
    def _throttle_if_needed(self) -> None:
        """Throttle processing if system resources are under pressure."""
        try:
            import psutil
            
            # Check CPU usage
            cpu_percent = psutil.cpu_percent(interval=0.1)
            if cpu_percent > 90:
                logger.debug(f"Throttling due to high CPU usage: {cpu_percent}%")
                time.sleep(2)  # Sleep for 2 seconds to reduce pressure
                
            # Check disk IO
            if hasattr(psutil, 'disk_io_counters'):
                disk_io = psutil.disk_io_counters()
                if disk_io and hasattr(disk_io, 'busy_time') and disk_io.busy_time > 80:
                    logger.debug("Throttling due to high disk IO")
                    time.sleep(1)  # Sleep for 1 second
        except ImportError:
            pass  # psutil not available
        except Exception as e:
            logger.debug(f"Error in throttle check: {str(e)}")
    
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
            # Skip backing up the backup service itself
            if self._is_backup_service(service_name):
                logger.info(f"Skipping backup of the backup service itself: {service_name}")
                return True
            
            # Run backup
            success = service.backup()
            return success
        except Exception as e:
            logger.error(f"Error during backup of {service_name}: {str(e)}")
            return False
        finally:
            # Always remove lock file
            self._remove_lock(lock_path)
    
    def _is_backup_service(self, service_name: str) -> bool:
        """
        Check if a service is the backup service itself using multiple methods.
        
        Args:
            service_name (str): Service name to check.
            
        Returns:
            bool: True if this is the backup service, False otherwise.
        """
        # Check environment variable for backup service name
        backup_service_names = os.environ.get('BACKUP_SERVICE_NAMES', 'container-backup,backup')
        backup_names = [name.strip().lower() for name in backup_service_names.split(',')]
        
        # Check current hostname
        try:
            import socket
            current_hostname = socket.gethostname().lower()
            if current_hostname == service_name.lower():
                logger.debug(f"Self-backup detection: service name matches hostname: {current_hostname}")
                return True
        except Exception as e:
            logger.debug(f"Could not determine current hostname: {str(e)}")
        
        # Check current container ID
        try:
            with open('/proc/self/cgroup', 'r') as f:
                for line in f:
                    if 'docker' in line:
                        current_container_id = line.split('/')[-1].strip()
                        # If the service has a container with this ID, it's us
                        for container in self.containers if hasattr(self, 'containers') else []:
                            if container.id == current_container_id:
                                logger.debug(f"Self-backup detection: found matching container ID")
                                return True
        except Exception as e:
            logger.debug(f"Could not determine current container ID: {str(e)}")
        
        # Check by name
        if service_name.lower() in backup_names:
            logger.debug(f"Self-backup detection: service name in backup_names list")
            return True
        
        return False

    def _is_excluded_service(self, service_name: str) -> bool:
        """
        Check if a service is excluded from backup via environment variable.
        
        Args:
            service_name (str): Service name to check.
            
        Returns:
            bool: True if excluded, False otherwise.
        """
        # Check environment variable for excluded services
        exclude_env = os.environ.get('EXCLUDE_FROM_BACKUP', '')
        if not exclude_env.strip():
            return False
            
        # Split by comma and strip whitespace
        excluded_services = [s.strip().lower() for s in exclude_env.split(',') if s.strip()]
        
        # Check if service name is in excluded list (case-insensitive)
        return service_name.lower() in excluded_services
    
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
        Run backup for a service with proper locking, self-backup detection,
        and exclusion filter checking.
        
        Args:
            service (ServiceBackup): Service backup instance.
            
        Returns:
            bool: True if successful, False otherwise.
        """
        service_name = service.service_name
        logger.debug(f"Running backup for service: {service_name}")
        
        # Check for self-backup
        if self._is_backup_service(service_name):
            logger.info(f"Skipping backup of the backup service itself: {service_name}")
            return True
        
        # Check if service is in exclusion list (double check)
        if self._is_excluded_service(service_name):
            logger.info(f"Service {service_name} is excluded from backup")
            return True
        
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
        Create a lock file for a service with process ID and improved timeout handling.
        
        Args:
            service_name (str): Service name.
            
        Returns:
            Path or None: Path to lock file if created, None otherwise.
        """
        lock_path = self.lock_dir / f"{service_name}.lock"
        
        # Check if lock already exists
        if lock_path.exists():
            try:
                # Read lock file to check if it's stale
                with open(lock_path, 'r') as f:
                    lock_data_str = f.read().strip()
                    
                # Parse lock data - handle both old and new format
                try:
                    # Try parsing as JSON (new format)
                    lock_data = json.loads(lock_data_str)
                    lock_time = lock_data.get('timestamp', 0)
                    lock_pid = lock_data.get('pid', 0)
                except json.JSONDecodeError:
                    # Old format or corrupted - treat as stale
                    logger.warning(f"Lock file for {service_name} has invalid format, treating as stale")
                    os.remove(lock_path)
                    return self._create_new_lock(service_name, lock_path)
                
                # Check if the process still exists
                process_running = False
                if lock_pid > 0:
                    try:
                        # Check if process exists (works on Unix-like systems)
                        if lock_pid != os.getpid():  # Skip check if it's our own process
                            os.kill(lock_pid, 0)
                            process_running = True
                    except OSError:
                        # Process does not exist
                        process_running = False
                
                # Check if lock is stale (older than 3 hours or process not running)
                if time.time() - lock_time > 3 * 3600 or not process_running:
                    logger.warning(f"Removing stale lock for {service_name} (PID: {lock_pid})")
                    os.remove(lock_path)
                    return self._create_new_lock(service_name, lock_path)
                else:
                    logger.warning(f"Service {service_name} is already being backed up (PID: {lock_pid})")
                    return None
                    
            except (json.JSONDecodeError, KeyError, FileNotFoundError) as e:
                # Lock file is corrupted or was removed - safe to replace
                logger.warning(f"Lock file for {service_name} is corrupted or was removed: {str(e)}")
                try:
                    if lock_path.exists():
                        os.remove(lock_path)
                except FileNotFoundError:
                    pass
                return self._create_new_lock(service_name, lock_path)
        
        # No existing lock, create a new one
        return self._create_new_lock(service_name, lock_path)
    
    def _create_new_lock(self, service_name: str, lock_path: Path) -> Optional[Path]:
        """
        Create a new lock file.
        
        Args:
            service_name (str): Service name.
            lock_path (Path): Path to lock file.
            
        Returns:
            Path or None: Path to lock file if created, None otherwise.
        """
        try:
            # Generate unique backup name
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{service_name}_{timestamp}.tar.gz"
            
            # Create lock file with process information
            lock_data = {
                'service': service_name,
                'backup_name': backup_name,
                'timestamp': time.time(),
                'pid': os.getpid(),  # Use actual process ID, not hardcoded 1
                'hostname': os.uname().nodename if hasattr(os, 'uname') else 'unknown'
            }
            
            # Write lock file as JSON for better parsing
            with open(lock_path, 'w') as f:
                f.write(json.dumps(lock_data))
            
            return lock_path
        except Exception as e:
            logger.error(f"Error creating lock file: {str(e)}")
            return None
    
    def _remove_lock(self, lock_path: Path) -> None:
        """
        Remove a lock file with improved error handling.
        
        Args:
            lock_path (Path): Path to lock file.
        """
        try:
            if lock_path and lock_path.exists():
                # Read lock file to log what we're removing
                try:
                    with open(lock_path, 'r') as f:
                        lock_data = json.loads(f.read())
                    logger.debug(f"Removing lock for service {lock_data.get('service')} (PID: {lock_data.get('pid')})")
                except Exception as e:
                    logger.debug(f"Could not read lock file details: {str(e)}")
                    
                # Remove the lock file
                os.remove(lock_path)
                logger.debug(f"Lock file removed: {lock_path}")
        except Exception as e:
            logger.error(f"Error removing lock file {lock_path}: {str(e)}")

    def _check_stale_locks(self) -> int:
        """
        Check for and remove stale lock files.
        
        A lock is considered stale if:
        - It's older than 3 hours
        - The process that created it is no longer running
        
        Returns:
            int: Number of stale locks removed
        """
        logger.debug("Checking for stale locks")
        stale_locks_removed = 0
        
        # Create lock directory if it doesn't exist
        os.makedirs(self.lock_dir, exist_ok=True)
        
        # Check all lock files
        for lock_file in self.lock_dir.glob('*.lock'):
            try:
                with open(lock_file, 'r') as f:
                    lock_data_str = f.read().strip()
                    
                # Try to parse as JSON
                try:
                    lock_data = json.loads(lock_data_str)
                    timestamp = lock_data.get('timestamp', 0)  # Default to 0 if not present
                    pid = lock_data.get('pid', 0)  # Default to 0 if not present
                except (json.JSONDecodeError, TypeError):
                    # Handle old format or invalid JSON
                    logger.warning(f"Lock file {lock_file} has invalid format, treating as stale")
                    os.remove(lock_file)
                    stale_locks_removed += 1
                    continue
                    
                # Check if lock is stale
                current_time = time.time()
                
                # Ensure timestamp is a number
                if timestamp is None:
                    timestamp = 0
                    
                is_stale = current_time - timestamp > 3 * 3600  # 3 hours
                
                # Check if process still exists
                process_exists = False
                if pid > 0:
                    try:
                        if pid != os.getpid():  # Don't check our own process
                            os.kill(pid, 0)
                            process_exists = True
                    except OSError:
                        # Process does not exist
                        pass
                        
                if is_stale or not process_exists:
                    service_name = os.path.basename(lock_file).replace('.lock', '')
                    logger.warning(f"Removing stale lock for {service_name} (PID: {pid})")
                    os.remove(lock_file)
                    stale_locks_removed += 1
            except Exception as e:
                logger.error(f"Error checking lock file {lock_file}: {str(e)}")
        
        return stale_locks_removed
    
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
