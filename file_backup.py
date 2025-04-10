#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
File backup module for service-oriented Docker backup system.
Manages application file backup operations.
"""

import os
import time
import tempfile
import tarfile
import shutil
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
    
    def _backup_path(self, path: str, temp_dir: str) -> bool:
        """
        Back up a single path.
        
        Args:
            path (str): Path to back up.
            temp_dir (str): Temporary directory to store backup.
            
        Returns:
            bool: True if successful, False otherwise.
        """
        logger.debug(f"Backing up path: {path}")
        
        # Handle root directory special case
        if path in [".", "/"]:
            # For root directory, use specific paths and exclusions
            if not self.exclusions:
                self.exclusions = [
                    "tmp/*", "proc/*", "sys/*", "dev/*", "run/*", "var/cache/*",
                    "var/tmp/*", "var/log/*", "var/lib/docker/*"
                ]
            
            # Set path to empty string for root
            path = ""
        
        # Normalize path
        norm_path = path.rstrip("/")
        
        # Check if path exists
        check_cmd = f"[ -e '{norm_path}' ] && echo 'EXISTS' || echo 'NOT_FOUND'"
        exit_code, output = exec_in_container(self.container, check_cmd)
        
        if "NOT_FOUND" in output:
            logger.warning(f"Path not found in container: {norm_path}")
            return False
        
        # Determine if this is a volume mount
        mounts = get_container_mounts(self.container)
        is_volume_mount = False
        
        for mount in mounts:
            container_path = mount.get("destination", "")
            if container_path and (container_path == norm_path or 
                                  norm_path.startswith(container_path + "/")):
                is_volume_mount = True
                logger.debug(f"Path {norm_path} is a volume mount")
                break
        
        # Back up the path
        if is_volume_mount:
            return self._backup_volume_mount(norm_path, temp_dir)
        else:
            return self._backup_container_path(norm_path, temp_dir)
    
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
            tar_cmd = f"tar -cf /tmp/volume_backup.tar -C '{os.path.dirname(volume) or /}' " \
                      f"{exclusion_args} '{os.path.basename(volume) or .}'"
            
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
    
    def _apply_exclusions(self, path: str) -> str:
        """
        Apply exclusion patterns to path.
        
        Args:
            path (str): Path to apply exclusions to.
            
        Returns:
            str: Tar exclusion arguments.
        """
        if not self.exclusions:
            return ""
        
        # Build exclusion arguments for tar
        exclusion_args = []
        
        for pattern in self.exclusions:
            # Normalize pattern
            pattern = pattern.replace('\\', '/')
            
            # Convert glob pattern to tar exclude pattern
            if pattern.startswith('/'):
                # Absolute path - make relative to the backup path
                if path == "" or path == "/":
                    # For root path, use pattern as is (without leading slash)
                    tar_pattern = pattern[1:] if pattern.startswith('/') else pattern
                else:
                    # For other paths, check if pattern is within this path
                    if pattern.startswith(path):
                        # Pattern is within this path, make relative to it
                        rel_pattern = pattern[len(path):]
                        if rel_pattern.startswith('/'):
                            rel_pattern = rel_pattern[1:]
                        tar_pattern = rel_pattern
                    else:
                        # Pattern is outside this path, skip it
                        continue
            else:
                # Relative path - use as is
                tar_pattern = pattern
            
            # Add exclusion argument
            exclusion_args.append(f"--exclude='{tar_pattern}'")
        
        return " ".join(exclusion_args)
    
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
            "/etc/hostname", "/etc/hosts", "/etc/resolv.conf"
        ]
        
        return any(path == sys_dir or path.startswith(sys_dir + "/") for sys_dir in system_dirs)
