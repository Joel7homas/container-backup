#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
File backup module for service-oriented Docker backup system.
Manages application file backup operations.
"""

import os
import time
import shutil
import tarfile
import fnmatch
import tempfile
from pathlib import Path
from typing import Dict, List, Any, Optional, Union, Tuple, Set

from logger import get_logger
from utils.docker_utils import exec_in_container, get_container_mounts
from utils.archive_utils import create_tar_gz

logger = get_logger(__name__)


class FileBackup:
    """Manages application file backup operations."""
    
    def __init__(self, container: Any, paths: Optional[List[str]] = None,
                exclusions: Optional[List[str]] = None):
        """
        Initialize file backup handler.
        
        Args:
            container (Container): Docker container object.
            paths (list, optional): List of paths to back up.
            exclusions (list, optional): List of exclusion patterns.
        """
        self.container = container
        self.paths = paths or []
        self.exclusions = exclusions or []
        
        # Auto-detect paths if none provided
        if not self.paths:
            detected_paths = self.detect_data_paths()
            self.paths = detected_paths
            logger.debug(f"Auto-detected paths for backup: {', '.join(detected_paths)}")
        
        logger.debug(f"Initialized file backup for {container.name} with {len(self.paths)} paths")
    
    def backup(self, output_path: str) -> bool:
        """
        Back up files to specified path.
        
        Args:
            output_path (str): Path to store backup.
            
        Returns:
            bool: True if successful, False otherwise.
        """
        if not self.paths:
            logger.warning(f"No paths to back up for container {self.container.name}")
            return False
        
        # Create output directory if it doesn't exist
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        logger.info(f"Starting file backup for {self.container.name}")
        
        # Create a temporary directory to store intermediate files
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                # Back up each path
                success_count = 0
                for path in self.paths:
                    path_success = self._backup_path(path, temp_dir)
                    if path_success:
                        success_count += 1
                
                # Check if at least one path was backed up successfully
                if success_count == 0:
                    logger.error(f"No paths were backed up successfully")
                    return False
                
                # Create final archive from all backed up paths
                logger.debug(f"Creating final archive at {output_path}")
                return create_tar_gz(temp_dir, output_path)
                
            except Exception as e:
                logger.error(f"Error during file backup: {str(e)}")
                return False
    
    def _backup_path(self, path: str, output_path: str) -> bool:
        """
        Back up a path with improved exclusion handling.
        
        Args:
            path (str): Path to back up.
            output_path (str): Path to store backup.
            
        Returns:
            bool: True if successful, False otherwise.
        """
        if not self._validate_path(path):
            logger.error(f"Invalid path: {path}")
            return False
            
        # Check if path should be excluded based on exclusion patterns
        if self._should_exclude_path(path):
            logger.info(f"Skipping excluded path: {path}")
            return True  # Return true as this is an expected skip, not an error
        
        try:
            logger.debug(f"Backing up path: {path}")
            
            # Create temporary directory for archiving
            with tempfile.TemporaryDirectory() as temp_dir:
                archive_path = os.path.join(temp_dir, "archive.tar.gz")
                
                # Apply exclusions for tar command
                exclusion_args = self._apply_exclusions(self.exclusions)
                
                # Build tar command with proper parameter handling
                tar_cmd = ["tar", "-czf", archive_path, "-C", os.path.dirname(path)]
                tar_cmd.extend(shlex.split(exclusion_args))
                tar_cmd.append(os.path.basename(path))
                
                # Execute tar command
                process = subprocess.run(
                    tar_cmd,
                    capture_output=True,
                    text=True,
                    check=False
                )
                
                if process.returncode != 0:
                    logger.error(f"Failed to create archive: {process.stderr}")
                    return False
                    
                # Move archive to output location
                shutil.copy(archive_path, output_path)
                logger.debug(f"Successfully backed up {path} to {output_path}")
                
                return True
                
        except Exception as e:
            logger.error(f"Error backing up path {path}: {str(e)}")
            return False
    
    def _backup_path_from_stopped_container(self, path: str, temp_dir: str) -> bool:
        """
        Back up a path from a stopped container using the Docker API instead of docker cp.
        
        Args:
            path (str): Path to back up.
            temp_dir (str): Temporary directory to store backup.
            
        Returns:
            bool: True if successful, False otherwise.
        """
        logger.info(f"Backing up path from stopped container {self.container.name} using Docker API: {path}")
        
        try:
            import io
            import tarfile
            
            # Create a target directory based on the path name
            target_dir = os.path.join(temp_dir, os.path.basename(path) or "root")
            os.makedirs(target_dir, exist_ok=True)
            
            # Get the container
            container_id = self.container.id
            container_path = path or "/"  # Use root if path is empty
            
            # Get archive from container using Docker API
            try:
                logger.debug(f"Getting archive from container {self.container.name} path: {container_path}")
                bits, stat = self.container.get_archive(container_path)
                
                # Create a file-like object from the generator
                fileobj = io.BytesIO()
                for chunk in bits:
                    fileobj.write(chunk)
                fileobj.seek(0)
                
                # Extract the tar data
                with tarfile.open(fileobj=fileobj, mode='r') as tar:
                    # Apply exclusion filters if needed
                    if self.exclusions:
                        logger.debug(f"Applying exclusions to archive: {', '.join(self.exclusions)}")
                        for member in tar.getmembers():
                            skip = False
                            for pattern in self.exclusions:
                                if fnmatch.fnmatch(member.name, pattern):
                                    skip = True
                                    break
                            
                            if not skip:
                                tar.extract(member, path=target_dir)
                    else:
                        # Extract everything
                        tar.extractall(path=target_dir)
                
                logger.info(f"Successfully backed up path {path} from stopped container {self.container.name}")
                return True
                
            except Exception as e:
                logger.error(f"Failed to get archive from container: {str(e)}")
                return False
                
        except Exception as e:
            logger.error(f"Error backing up path {path} from stopped container {self.container.name}: {str(e)}")
            return False
    
    def _should_exclude_path(self, path: str) -> bool:
        """
        Check if a path should be excluded based on exclusion patterns.
        
        Args:
            path (str): Path to check.
            
        Returns:
            bool: True if path should be excluded, False otherwise.
        """
        # Get path exclusion patterns from environment
        exclude_paths_env = os.environ.get('EXCLUDE_MOUNT_PATHS', '')
        exclude_patterns = []
        
        # Parse both comma and space separated values
        if exclude_paths_env:
            # Handle comma-separated list
            for item in exclude_paths_env.split(','):
                # Also handle space-separated items within commas
                for pattern in item.split():
                    if pattern:
                        exclude_patterns.append(pattern.strip())
        
        # Add common patterns that should generally be excluded
        common_excludes = [
            '/mnt/media',
            '/media',
            '/backups',
            '/mnt/backups',
            '/cache',
            '/tmp',
            '/var/lib/docker'
        ]
        
        # Add paths that don't exist in most containers or cause false warnings
        nonexistent_paths = [
            '/data',                # Common path that might not exist
            '/app/data',            # Common path that might not exist
            '/config',              # Common path that might not exist
            '/etc/localtime',       # Often mounted but not needed for backup
            '/etc/timezone',        # Often mounted but not needed for backup
            '/var/lib/bluetooth',   # Often causes permission errors
            '/root',                # Home directory that might be restricted
            '/home'                 # Home directories often have restricted permissions
        ]
        
        # Combine all exclusion patterns
        all_patterns = exclude_patterns + common_excludes + nonexistent_paths
        
        # Check if path matches any exclusion pattern
        for pattern in all_patterns:
            if pattern in path:
                logger.debug(f"Path {path} matches exclusion pattern: {pattern}")
                return True
        
        # Check for paths that might be problematic with NFS
        if '/mnt/docker/' in path:
            # For paths on NFS mounts, check if the path is actually readable
            try:
                # Try a lightweight check first - exists and listdir access
                if not os.path.exists(path):
                    logger.debug(f"Path does not exist: {path}")
                    return True
                    
                # Try to list directory contents as an access check
                if os.path.isdir(path):
                    os.listdir(path)
            except (PermissionError, OSError) as e:
                logger.warning(f"Cannot access {path}: {str(e)}, excluding from backup")
                return True
        
        # Check for paths in Docker volume storage that might need special handling
        if '/var/lib/docker/volumes/' in path:
            # For Docker volumes, we need specific access permissions
            # Check if the path is accessible before attempting backup
            try:
                if not os.access(path, os.R_OK):
                    logger.warning(f"Path {path} is not readable, excluding from backup")
                    return True
            except (OSError, PermissionError):
                logger.warning(f"Permission error checking {path}, excluding from backup")
                return True
        
        return False

    def _backup_volume_mount(self, volume: str, output_dir: str) -> bool:
            """
            Back up a volume mount.
            
            Args:
                volume (str): Volume path.
                output_dir (str): Path to store backup.
                
            Returns:
                bool: True if successful, False otherwise.
            """
            try:
                # Create target directory
                target_dir = os.path.join(output_dir, os.path.basename(volume) or "root")
                os.makedirs(target_dir, exist_ok=True)
                
                # Create tar file in container
                exclusion_args = self._apply_exclusions(volume)
                parent_dir = os.path.dirname(volume) or "/"
                base_name = os.path.basename(volume) or "."
                tar_cmd = f"tar -cf /tmp/volume_backup.tar -C '{parent_dir}' " \
                          f"{exclusion_args} '{base_name}'"
                
                logger.debug(f"Creating tar file in container: {tar_cmd}")
                exit_code, output = exec_in_container(self.container, tar_cmd)
                
                if exit_code != 0:
                    logger.error(f"Failed to create tar file in container: {output}")
                    return False
                
                # Get tar file content from container
                cat_cmd = "cat /tmp/volume_backup.tar"
                exit_code, tar_data = exec_in_container(self.container, cat_cmd)
                
                if exit_code != 0 or not tar_data:
                    logger.error("Failed to retrieve tar data from container")
                    exec_in_container(self.container, "rm -f /tmp/volume_backup.tar")
                    return False
                
                # Write tar data to temporary file
                temp_tar = os.path.join(output_dir, "temp_volume.tar")
                with open(temp_tar, 'wb') as f:
                    f.write(tar_data.encode('utf-8', errors='replace'))
                
                # Extract tar file to target directory
                with tarfile.open(temp_tar, 'r') as tar:
                    tar.extractall(path=target_dir)
                
                # Clean up
                os.remove(temp_tar)
                exec_in_container(self.container, "rm -f /tmp/volume_backup.tar")
                
                logger.info(f"Successfully backed up volume mount: {volume}")
                return True
                
            except Exception as e:
                logger.error(f"Error backing up volume mount {volume}: {str(e)}")
                # Clean up in container anyway
                exec_in_container(self.container, "rm -f /tmp/volume_backup.tar")
                return False
    
    def _backup_container_path(self, path: str, output_dir: str) -> bool:
        """
        Back up a path within the container.
        
        Args:
            path (str): Path to back up.
            output_dir (str): Path to store backup.
            
        Returns:
            bool: True if successful, False otherwise.
        """
        try:
            # Create target directory
            target_dir = os.path.join(output_dir, os.path.basename(path) or "root")
            os.makedirs(target_dir, exist_ok=True)
            
            # Determine parent and base paths
            parent_path = os.path.dirname(path) or "/"
            base_name = os.path.basename(path) or "."
            
            # Create tar file in container with exclusions
            exclusion_args = self._apply_exclusions(path)
            tar_cmd = f"tar -cf /tmp/path_backup.tar -C '{parent_path}' {exclusion_args} '{base_name}'"
            
            logger.debug(f"Creating tar file in container: {tar_cmd}")
            exit_code, output = exec_in_container(self.container, tar_cmd)
            
            if exit_code != 0:
                logger.error(f"Failed to create tar file in container: {output}")
                return False
            
            # Get tar file content from container
            cat_cmd = "cat /tmp/path_backup.tar"
            exit_code, tar_data = exec_in_container(self.container, cat_cmd)
            
            if exit_code != 0 or not tar_data:
                logger.error("Failed to retrieve tar data from container")
                exec_in_container(self.container, "rm -f /tmp/path_backup.tar")
                return False
            
            # Write tar data to temporary file
            temp_tar = os.path.join(output_dir, "temp_path.tar")
            with open(temp_tar, 'wb') as f:
                f.write(tar_data.encode('utf-8', errors='replace'))
            
            # Extract tar file to target directory
            with tarfile.open(temp_tar, 'r') as tar:
                tar.extractall(path=target_dir)
            
            # Clean up
            os.remove(temp_tar)
            exec_in_container(self.container, "rm -f /tmp/path_backup.tar")
            
            logger.info(f"Successfully backed up container path: {path}")
            return True
            
        except Exception as e:
            logger.error(f"Error backing up container path {path}: {str(e)}")
            # Clean up in container anyway
            exec_in_container(self.container, "rm -f /tmp/path_backup.tar")
            return False
    
    def _apply_exclusions(self, exclusions: List[str]) -> str:
        """
        Apply exclusion patterns to generate tar exclude arguments.
        
        Args:
            exclusions (list): List of exclusion patterns.
            
        Returns:
            str: Arguments for tar command.
        """
        # Start with empty exclusions string
        exclusion_args = ""
        
        # If exclusions list is provided, add each exclusion
        if exclusions:
            for pattern in exclusions:
                # Validate and sanitize exclusion pattern
                if self._validate_path(pattern):
                    # Use shlex.quote to properly escape the pattern
                    exclusion_args += f" --exclude={shlex.quote(pattern)}"
        
        # Add global exclusions
        global_exclusions = [
            "*/cache/*", 
            "*/tmp/*", 
            "*/logs/*.log",
            "*/backups/*",
            "*/.git/*"
        ]
        
        for pattern in global_exclusions:
            exclusion_args += f" --exclude={shlex.quote(pattern)}"
        
        return exclusion_args
    
    def detect_data_paths(self) -> List[str]:
        """
        Detect important data paths in container.
        
        Returns:
            list: List of detected data paths.
        """
        paths = []
        
        try:
            # Check for common data directories
            common_paths = [
                "/data", "/config", "/app/data", "/var/lib/mysql", 
                "/var/lib/postgresql/data", "/var/www",
                "/app/config", "/home/appuser/data", "/opt/app/data"
            ]
            
            for path in common_paths:
                check_cmd = f"[ -d '{path}' ] && echo 'EXISTS' || echo 'NOT_FOUND'"
                exit_code, output = exec_in_container(self.container, check_cmd)
                
                if "EXISTS" in output:
                    paths.append(path)
            
            # Check for volume mounts
            mounts = get_container_mounts(self.container)
            
            for mount in mounts:
                destination = mount.get("destination", "")
                
                # Skip system directories
                if destination and not self._is_system_directory(destination):
                    paths.append(destination)
            
            # If no paths found, use root
            if not paths:
                paths.append("/")
            
            return paths
            
        except Exception as e:
            logger.error(f"Error detecting data paths: {str(e)}")
            return ["/"]
    

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
