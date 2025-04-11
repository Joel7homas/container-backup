#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Database backup module for service-oriented Docker backup system.
Handles database-specific backup operations for different database types.
"""

import os
import gzip
import time
import shutil
from pathlib import Path
from typing import Dict, List, Any, Optional, Union, Tuple

from logger import get_logger
from utils.docker_utils import exec_in_container
from utils.credential_utils import extract_database_credentials, mask_sensitive_data

logger = get_logger(__name__)


class DatabaseBackup:
    """Handles database-specific backup operations."""
    
    def __init__(self, container: Any, credentials: Optional[Dict[str, str]] = None, 
                db_type: Optional[str] = None, config: Optional[Dict[str, Any]] = None):
        """
        Initialize database backup handler.
        
        Args:
            container (Container): Docker container object.
            credentials (dict, optional): Database credentials.
            db_type (str, optional): Type of database (postgres, mysql, sqlite, etc.).
            config (dict, optional): Additional configuration.
        """
        self.container = container
        self.credentials = credentials or {}
        self.db_type = db_type or self._detect_db_type()
        self.config = config or {}
        
        logger.debug(f"Initialized database backup for {container.name}, type: {self.db_type}")
    
    def _detect_db_type(self) -> Optional[str]:
        """
        Detect database type from container image.
        
        Returns:
            str or None: Detected database type or None if unknown.
        """
        try:
            image = self.container.image.tags[0].lower() if self.container.image.tags else ""
            
            if any(db_type in image for db_type in ["postgres", "pgvecto"]):
                return "postgres"
            elif any(db_type in image for db_type in ["mysql", "mariadb"]):
                return "mysql"
            elif "mongodb" in image or "mongo" in image:
                return "mongodb"
            elif "redis" in image:
                return "redis"
            elif "sqlite" in image:
                return "sqlite"
            
            # If no match, check for SQLite files
            cmd = "find / -name '*.sqlite' -o -name '*.db' -o -name '*.sqlite3' | head -1"
            exit_code, output = exec_in_container(self.container, cmd)
            if exit_code == 0 and output.strip():
                return "sqlite"
                
            return None
            
        except Exception as e:
            logger.error(f"Error detecting database type: {str(e)}")
            return None
    
    def backup(self, output_path: str) -> bool:
            """
            Back up database to specified path.
            
            Args:
                output_path (str): Path to store backup.
                
            Returns:
                bool: True if successful, False otherwise.
            """
            if not self.db_type:
                logger.error(f"Unknown database type for container {self.container.name}")
                return False
            
            # Validate container status
            if not hasattr(self.container, "status"):
                logger.error(f"Invalid container object for {self.container.name}")
                return False
                
            # Validate output path
            if not output_path:
                logger.error("Output path cannot be empty")
                return False
                
            # Create output directory if it doesn't exist
            try:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
            except PermissionError:
                logger.error(f"Permission denied creating directory: {os.path.dirname(output_path)}")
                return False
            except OSError as e:
                logger.error(f"Failed to create output directory: {str(e)}")
                return False
            
            logger.info(f"Starting backup of {self.db_type} database in {self.container.name}")
            
            # Track temporary files for cleanup in case of failure
            temp_files = []
            
            try:
                if self.db_type == "postgres":
                    return self._backup_postgres(output_path)
                elif self.db_type in ["mysql", "mariadb"]:
                    return self._backup_mysql(output_path)
                elif self.db_type == "sqlite":
                    return self._backup_sqlite(output_path)
                elif self.db_type == "mongodb":
                    return self._backup_mongodb(output_path)
                elif self.db_type == "redis":
                    return self._backup_redis(output_path)
                else:
                    logger.error(f"Unsupported database type: {self.db_type}")
                    return False
            except Exception as e:
                logger.error(f"Error backing up {self.db_type} database in {self.container.name}: {str(e)}")
                
                # Attempt to clean up any temporary files
                if os.path.exists(output_path):
                    try:
                        os.remove(output_path)
                        logger.debug(f"Removed incomplete backup file: {output_path}")
                    except Exception as cleanup_error:
                        logger.warning(f"Failed to clean up incomplete backup file: {str(cleanup_error)}")
                
                return False
    
    def _backup_postgres(self, output_path: str) -> bool:
            """
            Back up PostgreSQL database.
            
            Args:
                output_path (str): Path to store backup.
                
            Returns:
                bool: True if successful, False otherwise.
            """
            # Verify credentials
            if not self.credentials.get('user') or not self.credentials.get('database'):
                logger.error("Missing required PostgreSQL credentials")
                return False
                
            # Validate credential values to prevent injection
            user = self.credentials.get('user', '')
            database = self.credentials.get('database', '')
            host = self.credentials.get('host', '')
            port = self.credentials.get('port', '')
            
            # Check for potentially dangerous characters in parameters
            for param_name, param_value in [
                ('user', user), 
                ('database', database),
                ('host', host)
            ]:
                if not isinstance(param_value, str):
                    logger.error(f"Invalid {param_name} parameter type: {type(param_value)}")
                    return False
                    
                if any(c in param_value for c in [';', '&&', '||', '`', '$', '|', '>', '<']):
                    logger.error(f"Invalid characters in {param_name} parameter")
                    return False
            
            # For numeric parameters, ensure they're actually numeric
            if port and not str(port).isdigit():
                logger.error(f"Port must be numeric: {port}")
                return False
                
            # Build pg_dump command with properly escaped parameters
            cmd = [
                "pg_dump",
                "-U", user
            ]
                
            # Add host if specified
            if host and host != 'localhost':
                cmd.extend(["-h", host])
            
            # Add port if specified
            if port:
                cmd.extend(["-p", str(port)])
            
            # Add database name
            cmd.append(database)
            
            # Convert command list to string for exec_in_container
            cmd_str = " ".join(cmd)
            
            # Set environment variables
            env = {}
            if self.credentials.get('password'):
                env["PGPASSWORD"] = self.credentials['password']
            
            # Execute backup command
            logger.debug(f"Executing PostgreSQL backup command: {cmd_str}")
            exit_code, output = exec_in_container(self.container, cmd_str, env)
            
            if exit_code != 0:
                logger.error(f"PostgreSQL backup failed: {output}")
                return False
            
            # Compress and save output
            try:
                # Create temporary file first
                temp_output_path = f"{output_path}.temp"
                with gzip.open(temp_output_path, 'wb') as f:
                    f.write(output.encode('utf-8'))
                
                # Move to final location
                shutil.move(temp_output_path, output_path)
                logger.info(f"PostgreSQL backup completed successfully: {output_path}")
                return True
            except Exception as e:
                logger.error(f"Error saving PostgreSQL backup: {str(e)}")
                # Clean up temporary file if it exists
                if os.path.exists(f"{output_path}.temp"):
                    try:
                        os.remove(f"{output_path}.temp")
                    except Exception:
                        pass
                return False
    
    def _backup_mysql(self, output_path: str) -> bool:
            """
            Back up MySQL/MariaDB database.
            
            Args:
                output_path (str): Path to store backup.
                
            Returns:
                bool: True if successful, False otherwise.
            """
            # Verify credentials
            if not self.credentials.get('user'):
                logger.error("Missing required MySQL credentials")
                return False
                
            # Validate credential values to prevent injection
            user = self.credentials.get('user', '')
            database = self.credentials.get('database', '')
            host = self.credentials.get('host', '')
            port = self.credentials.get('port', '')
            
            # Check for potentially dangerous characters in parameters
            for param_name, param_value in [
                ('user', user), 
                ('database', database),
                ('host', host)
            ]:
                if param_value and not isinstance(param_value, str):
                    logger.error(f"Invalid {param_name} parameter type: {type(param_value)}")
                    return False
                    
                if param_value and any(c in param_value for c in [';', '&&', '||', '`', '$', '|', '>', '<']):
                    logger.error(f"Invalid characters in {param_name} parameter")
                    return False
            
            # For numeric parameters, ensure they're actually numeric
            if port and not str(port).isdigit():
                logger.error(f"Port must be numeric: {port}")
                return False
                
            # Build mysqldump command parts (without password)
            cmd_parts = ["mysqldump", "-u", user]
            
            # Add host if specified
            if host and host != 'localhost':
                cmd_parts.extend(["-h", host])
            
            # Add port if specified
            if port:
                cmd_parts.extend(["-P", str(port)])
            
            # Add database name or use all databases
            if database:
                cmd_parts.append(database)
            else:
                cmd_parts.append("--all-databases")
            
            # Add extra options
            cmd_parts.extend(["--single-transaction", "--quick", "--lock-tables=false"])
            
            # Convert to string and add password separately for security
            cmd = " ".join(cmd_parts)
            
            # Add password if specified (not logging this part)
            if self.credentials.get('password'):
                # Using a more secure approach with MYSQL_PWD env var instead of command line
                env = {"MYSQL_PWD": self.credentials['password']}
            else:
                env = {}
            
            # Execute backup command
            logger.debug(f"Executing MySQL backup command (without password)")
            exit_code, output = exec_in_container(self.container, cmd, env)
            
            if exit_code != 0:
                logger.error(f"MySQL backup failed: {output}")
                return False
            
            # Compress and save output
            try:
                # Create temporary file first
                temp_output_path = f"{output_path}.temp"
                with gzip.open(temp_output_path, 'wb') as f:
                    f.write(output.encode('utf-8'))
                
                # Move to final location
                shutil.move(temp_output_path, output_path)
                logger.info(f"MySQL backup completed successfully: {output_path}")
                return True
            except Exception as e:
                logger.error(f"Error saving MySQL backup: {str(e)}")
                # Clean up temporary file if it exists
                if os.path.exists(f"{output_path}.temp"):
                    try:
                        os.remove(f"{output_path}.temp")
                    except Exception:
                        pass
                return False

    def _backup_sqlite(self, output_path: str) -> bool:
        """
        Back up SQLite database.
        
        Args:
            output_path (str): Path to store backup.
            
        Returns:
            bool: True if successful, False otherwise.
        """
        # Find SQLite database files
        cmd = "find / -name '*.sqlite' -o -name '*.db' -o -name '*.sqlite3'"
        exit_code, output = exec_in_container(self.container, cmd)
        
        if exit_code != 0 or not output.strip():
            logger.error(f"Failed to find SQLite database files: {output}")
            return False
        
        db_files = output.strip().split('\n')
        
        # If specific database specified in credentials, filter the list
        if self.credentials.get('database') and self.credentials['database'] in db_files:
            db_files = [self.credentials['database']]
        
        if not db_files:
            logger.error("No SQLite database files found")
            return False
        
        # Use the first database file found if multiple
        db_file = db_files[0]
        if len(db_files) > 1:
            logger.warning(f"Multiple SQLite databases found, using: {db_file}")
        
        # Create a temporary copy to ensure consistent backup
        temp_file = "/tmp/sqlite_backup_temp.db"
        
        try:
            # Create a backup using SQLite's backup mechanism
            backup_cmd = f"sqlite3 '{db_file}' '.backup '{temp_file}''"
            exit_code, output = exec_in_container(self.container, backup_cmd)
            
            if exit_code != 0:
                logger.error(f"SQLite backup command failed: {output}")
                return False
            
            # Get the temporary file from the container
            logger.debug(f"Copying SQLite database from container")
            
            # Create a tar archive of the file in the container
            tar_cmd = f"tar -cf /tmp/sqlite_backup.tar -C /tmp sqlite_backup_temp.db"
            exit_code, _ = exec_in_container(self.container, tar_cmd)
            
            if exit_code != 0:
                logger.error("Failed to create tar archive in container")
                return False
            
            # Get raw data from the container
            cat_cmd = "cat /tmp/sqlite_backup.tar"
            exit_code, tar_data = exec_in_container(self.container, cat_cmd)
            
            if exit_code != 0 or not tar_data:
                logger.error("Failed to retrieve tar data from container")
                return False
            
            # Write tar file locally
            temp_tar = f"{output_path}.tar"
            with open(temp_tar, 'wb') as f:
                f.write(tar_data.encode('utf-8', errors='replace'))
            
            # Extract SQLite file from tar
            import tarfile
            with tarfile.open(temp_tar, 'r') as tar:
                sqlite_content = tar.extractfile('sqlite_backup_temp.db').read()
            
            # Compress and save the SQLite file
            with gzip.open(output_path, 'wb') as f:
                f.write(sqlite_content)
            
            # Clean up temporary file
            os.remove(temp_tar)
            
            # Clean up in container
            exec_in_container(self.container, "rm -f /tmp/sqlite_backup.tar /tmp/sqlite_backup_temp.db")
            
            logger.info(f"SQLite backup completed successfully: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error during SQLite backup: {str(e)}")
            # Attempt to clean up in container anyway
            exec_in_container(self.container, "rm -f /tmp/sqlite_backup.tar /tmp/sqlite_backup_temp.db")
            return False
    
    def _backup_mongodb(self, output_path: str) -> bool:
        """
        Back up MongoDB database.
        
        Args:
            output_path (str): Path to store backup.
            
        Returns:
            bool: True if successful, False otherwise.
        """
        # Create temporary directory in container
        temp_dir = "/tmp/mongodb_backup"
        exec_in_container(self.container, f"mkdir -p {temp_dir}")
        
        # Build mongodump command
        cmd = "mongodump --out=" + temp_dir
        
        # Add authentication if provided
        if self.credentials.get('user') and self.credentials.get('password'):
            cmd += f" --username={self.credentials['user']} --password={self.credentials['password']}"
            
            # Add authentication database if provided
            if self.credentials.get('authSource'):
                cmd += f" --authenticationDatabase={self.credentials['authSource']}"
        
        # Add host and port if specified
        if self.credentials.get('host') and self.credentials['host'] != 'localhost':
            cmd += f" --host={self.credentials['host']}"
        
        if self.credentials.get('port'):
            cmd += f" --port={self.credentials['port']}"
        
        # Add database name if specified
        if self.credentials.get('database'):
            cmd += f" --db={self.credentials['database']}"
        
        # Execute backup command
        logger.debug(f"Executing MongoDB backup command")  # Don't log full command with password
        exit_code, output = exec_in_container(self.container, cmd)
        
        if exit_code != 0:
            logger.error(f"MongoDB backup failed: {output}")
            exec_in_container(self.container, f"rm -rf {temp_dir}")
            return False
        
        try:
            # Create tar archive in container
            tar_cmd = f"tar -cf /tmp/mongodb_backup.tar -C /tmp mongodb_backup"
            exit_code, _ = exec_in_container(self.container, tar_cmd)
            
            if exit_code != 0:
                logger.error("Failed to create MongoDB backup archive")
                exec_in_container(self.container, f"rm -rf {temp_dir} /tmp/mongodb_backup.tar")
                return False
            
            # Get tar data from container
            cat_cmd = "cat /tmp/mongodb_backup.tar"
            exit_code, tar_data = exec_in_container(self.container, cat_cmd)
            
            if exit_code != 0 or not tar_data:
                logger.error("Failed to retrieve MongoDB backup data")
                exec_in_container(self.container, f"rm -rf {temp_dir} /tmp/mongodb_backup.tar")
                return False
            
            # Compress and save output
            with gzip.open(output_path, 'wb') as f:
                f.write(tar_data.encode('utf-8', errors='replace'))
            
            # Clean up in container
            exec_in_container(self.container, f"rm -rf {temp_dir} /tmp/mongodb_backup.tar")
            
            logger.info(f"MongoDB backup completed successfully: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving MongoDB backup: {str(e)}")
            exec_in_container(self.container, f"rm -rf {temp_dir} /tmp/mongodb_backup.tar")
            return False
    
    def _backup_redis(self, output_path: str) -> bool:
        """
        Back up Redis database.
        
        Args:
            output_path (str): Path to store backup.
            
        Returns:
            bool: True if successful, False otherwise.
        """
        # Determine backup approach based on Redis configuration
        # First, try RDB file if available
        rdb_path = "/data/dump.rdb"
        check_cmd = f"ls {rdb_path} 2>/dev/null || echo 'NOT_FOUND'"
        exit_code, check_output = exec_in_container(self.container, check_cmd)
        
        if exit_code == 0 and "NOT_FOUND" not in check_output:
            # RDB file exists, copy it
            try:
                # Create tar archive of RDB file
                tar_cmd = f"tar -cf /tmp/redis_backup.tar {rdb_path}"
                exit_code, _ = exec_in_container(self.container, tar_cmd)
                
                if exit_code != 0:
                    logger.error("Failed to create Redis RDB backup archive")
                    return False
                
                # Get tar data from container
                cat_cmd = "cat /tmp/redis_backup.tar"
                exit_code, tar_data = exec_in_container(self.container, cat_cmd)
                
                if exit_code != 0 or not tar_data:
                    logger.error("Failed to retrieve Redis RDB backup data")
                    exec_in_container(self.container, "rm -f /tmp/redis_backup.tar")
                    return False
                
                # Compress and save output
                with gzip.open(output_path, 'wb') as f:
                    f.write(tar_data.encode('utf-8', errors='replace'))
                
                # Clean up in container
                exec_in_container(self.container, "rm -f /tmp/redis_backup.tar")
                
                logger.info(f"Redis RDB backup completed successfully: {output_path}")
                return True
                
            except Exception as e:
                logger.error(f"Error saving Redis RDB backup: {str(e)}")
                exec_in_container(self.container, "rm -f /tmp/redis_backup.tar")
                return False
        
        # If RDB not available or failed, try using redis-cli
        logger.info("RDB file not available, using redis-cli for backup")
        
        # Build redis-cli command
        cmd = "redis-cli --rdb /tmp/redis_backup.rdb"
        
        # Add authentication if provided
        if self.credentials.get('password'):
            cmd += f" -a {self.credentials['password']}"
        
        # Add host and port if specified
        if self.credentials.get('host') and self.credentials['host'] != 'localhost':
            cmd += f" -h {self.credentials['host']}"
        
        if self.credentials.get('port'):
            cmd += f" -p {self.credentials['port']}"
        
        # Execute backup command
        logger.debug(f"Executing Redis backup command")  # Don't log full command with password
        exit_code, output = exec_in_container(self.container, cmd)
        
        if exit_code != 0:
            logger.error(f"Redis backup failed: {output}")
            return False
        
        try:
            # Get the RDB file from the container
            cat_cmd = "cat /tmp/redis_backup.rdb"
            exit_code, rdb_data = exec_in_container(self.container, cat_cmd)
            
            if exit_code != 0 or not rdb_data:
                logger.error("Failed to retrieve Redis backup data")
                exec_in_container(self.container, "rm -f /tmp/redis_backup.rdb")
                return False
            
            # Compress and save output
            with gzip.open(output_path, 'wb') as f:
                f.write(rdb_data.encode('utf-8', errors='replace'))
            
            # Clean up in container
            exec_in_container(self.container, "rm -f /tmp/redis_backup.rdb")
            
            logger.info(f"Redis backup completed successfully: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving Redis backup: {str(e)}")
            exec_in_container(self.container, "rm -f /tmp/redis_backup.rdb")
            return False
    
    def get_credentials_from_environment(self, env_vars: Dict[str, str], 
                                       stack_name: Optional[str] = None) -> Dict[str, str]:
        """
        Extract database credentials from environment variables.
        
        Args:
            env_vars (dict): Dictionary of environment variables.
            stack_name (str, optional): Name of the stack.
            
        Returns:
            dict: Dictionary of credentials.
        """
        if not self.db_type:
            logger.error("Cannot extract credentials without database type")
            return {}
        
        credentials = extract_database_credentials(env_vars, self.db_type, stack_name)
        
        # Log credentials (masked)
        masked_credentials = mask_sensitive_data(credentials)
        logger.debug(f"Extracted credentials: {masked_credentials}")
        
        return credentials
