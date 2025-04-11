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

    def _validate_path(self, path: str) -> bool:
        """
        Validate a path to ensure it's safe to use in commands.
        
        Args:
            path (str): Path to validate.
            
        Returns:
            bool: True if path is valid, False otherwise.
        """
        if not isinstance(path, str):
            logger.error(f"Invalid path type: {type(path)}")
            return False
        
        # Check for potentially dangerous characters
        dangerous_chars = [';', '&&', '||', '`', '$', '|', '>', '<', '*', '?', '[', ']']
        if any(c in path for c in dangerous_chars):
            logger.error(f"Path contains dangerous characters: {path}")
            return False
        
        return True
    
    def _validate_credential(self, key: str, value: Any) -> bool:
        """
        Validate a credential value to prevent injection.
        
        Args:
            key (str): Credential key (user, password, etc.)
            value (Any): Credential value to validate.
            
        Returns:
            bool: True if value is valid, False otherwise.
        """
        if key in ['port']:
            # Port should be numeric
            if isinstance(value, int):
                return 1 <= value <= 65535
            elif isinstance(value, str) and value.isdigit():
                port = int(value)
                return 1 <= port <= 65535
            return False
        
        # String credentials
        if not isinstance(value, str):
            logger.error(f"Invalid {key} type: {type(value)}")
            return False
        
        # Check for dangerous shell characters
        dangerous_chars = [';', '&&', '||', '`', '|', '>', '<']
        if any(c in value for c in dangerous_chars):
            logger.error(f"{key} contains dangerous characters")
            return False
        
        return True
    
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
        Back up SQLite database with improved security and error handling.
        
        Args:
            output_path (str): Path to store backup.
            
        Returns:
            bool: True if successful, False otherwise.
        """
        # Create temporary directory for consistent backups
        temp_dir = None
        temp_files = []
        
        try:
            import tempfile
            temp_dir = tempfile.mkdtemp(prefix="sqlite_backup_")
            
            # Find SQLite database files
            cmd = "find / -name '*.sqlite' -o -name '*.db' -o -name '*.sqlite3' 2>/dev/null"
            exit_code, output = exec_in_container(self.container, cmd)
            
            if exit_code != 0:
                logger.error(f"Failed to find SQLite database files: {output}")
                return False
            
            db_files = [file for file in output.strip().split('\n') if file.strip()]
            
            # If specific database specified in credentials, filter the list
            if self.credentials.get('database') and isinstance(self.credentials['database'], str):
                db_path = self.credentials['database']
                # Validate database path
                if not self._validate_path(db_path):
                    logger.error(f"Invalid database path: {db_path}")
                    return False
                    
                # Check if path exists in the list
                matching_files = [f for f in db_files if f == db_path]
                if matching_files:
                    db_files = matching_files
                else:
                    logger.warning(f"Specified database not found: {db_path}, using discovered databases")
            
            if not db_files:
                logger.error("No SQLite database files found")
                return False
            
            # Use the first database file found if multiple
            db_file = db_files[0]
            if len(db_files) > 1:
                logger.warning(f"Multiple SQLite databases found, using: {db_file}")
            
            # Validate database path
            if not self._validate_path(db_file):
                logger.error(f"Invalid database path: {db_file}")
                return False
            
            # Use consistent paths in container
            container_temp = "/tmp/sqlite_backup"
            container_backup_db = f"{container_temp}/backup.db"
            container_tar = f"{container_temp}/sqlite_backup.tar"
            
            # Create temporary directory in container
            mkdir_cmd = f"mkdir -p {container_temp}"
            exit_code, _ = exec_in_container(self.container, mkdir_cmd)
            if exit_code != 0:
                logger.error(f"Failed to create temporary directory in container")
                return False
            
            # Record for cleanup
            temp_files.extend([container_backup_db, container_tar, container_temp])
            
            # Create a backup using SQLite's backup mechanism
            # Ensure path is properly escaped and quoted for security
            backup_cmd = f"sqlite3 '{db_file.replace(\"'\", \"''\")}' '.backup \"{container_backup_db}\"'"
            exit_code, output = exec_in_container(self.container, backup_cmd)
            
            if exit_code != 0:
                logger.error(f"SQLite backup command failed: {output}")
                return False
            
            # Create a tar archive in container
            tar_cmd = f"tar -cf {container_tar} -C $(dirname {container_backup_db}) $(basename {container_backup_db})"
            exit_code, _ = exec_in_container(self.container, tar_cmd)
            
            if exit_code != 0:
                logger.error("Failed to create tar archive in container")
                return False
            
            # Get tar data from container
            cat_cmd = f"cat {container_tar}"
            exit_code, tar_data = exec_in_container(self.container, cat_cmd)
            
            if exit_code != 0 or not tar_data:
                logger.error("Failed to retrieve tar data from container")
                return False
            
            # Write tar data to local temporary file
            local_temp_tar = os.path.join(temp_dir, "sqlite_backup.tar")
            with open(local_temp_tar, 'wb') as f:
                f.write(tar_data.encode('utf-8', errors='replace'))
            
            # Extract SQLite file from tar
            try:
                import tarfile
                with tarfile.open(local_temp_tar, 'r') as tar:
                    sqlite_content = tar.extractfile(os.path.basename(container_backup_db)).read()
            except Exception as e:
                logger.error(f"Failed to extract SQLite database from archive: {str(e)}")
                return False
            
            # Write to temporary output file first
            temp_output_path = f"{output_path}.tmp"
            with gzip.open(temp_output_path, 'wb') as f:
                f.write(sqlite_content)
            
            # Atomically move to final location
            shutil.move(temp_output_path, output_path)
            
            logger.info(f"SQLite backup completed successfully: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error during SQLite backup: {str(e)}")
            # Attempt to cleanup temporary output file
            if 'temp_output_path' in locals() and os.path.exists(temp_output_path):
                try:
                    os.remove(temp_output_path)
                except Exception:
                    pass
            return False
            
        finally:
            # Clean up in container
            if temp_files:
                cleanup_cmd = f"rm -rf {' '.join(temp_files)}"
                exec_in_container(self.container, cleanup_cmd)
            
            # Clean up local temporary directory
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.warning(f"Failed to clean up temporary directory: {str(e)}")
    
    def _backup_mongodb(self, output_path: str) -> bool:
        """
        Back up MongoDB database with improved security and error handling.
        
        Args:
            output_path (str): Path to store backup.
            
        Returns:
            bool: True if successful, False otherwise.
        """
        # Create temporary directory for consistent backups
        temp_dir = None
        temp_files = []
        
        try:
            temp_dir = tempfile.mkdtemp(prefix="mongodb_backup_")
            
            # Define consistent paths in container
            container_temp = "/tmp/mongodb_backup"
            container_tar = "/tmp/mongodb_backup.tar"
            
            # Create temporary directory in container
            mkdir_cmd = f"mkdir -p {container_temp}"
            exit_code, _ = exec_in_container(self.container, mkdir_cmd)
            
            if exit_code != 0:
                logger.error(f"Failed to create temporary directory in container")
                return False
            
            # Record for cleanup
            temp_files.extend([container_temp, container_tar])
            
            # Validate credentials
            credentials = {}
            for key in ['user', 'password', 'host', 'port', 'database', 'authSource']:
                if key in self.credentials and self.credentials[key]:
                    # Validate to prevent injection
                    if not self._validate_credential(key, self.credentials[key]):
                        logger.error(f"Invalid {key} parameter in MongoDB credentials")
                        return False
                    credentials[key] = self.credentials[key]
            
            # Build mongodump command arguments securely
            cmd_parts = ["mongodump", f"--out={container_temp}"]
            
            # Add authentication if provided
            if credentials.get('user') and credentials.get('password'):
                cmd_parts.append(f"--username={credentials['user']}")
                # Password will be passed via environment variable
            
            # Add authentication database if provided
            if credentials.get('authSource'):
                cmd_parts.append(f"--authenticationDatabase={credentials['authSource']}")
            
            # Add host and port if specified
            if credentials.get('host') and credentials['host'] != 'localhost':
                cmd_parts.append(f"--host={credentials['host']}")
            
            if credentials.get('port'):
                cmd_parts.append(f"--port={credentials['port']}")
            
            # Add database name if specified
            if credentials.get('database'):
                cmd_parts.append(f"--db={credentials['database']}")
            
            # Join command parts into a secure command string
            cmd = " ".join(cmd_parts)
            
            # Set up environment variables for sensitive data
            env = {}
            if credentials.get('password'):
                env["MONGO_PASSWORD"] = credentials['password']
                # Modified command to use environment variable
                cmd = cmd.replace("--username=", "MONGO_PASSWORD=\"$MONGO_PASSWORD\" --username=")
            
            # Execute backup command
            logger.debug(f"Executing MongoDB backup command (without password)")
            exit_code, output = exec_in_container(self.container, cmd, env)
            
            if exit_code != 0:
                logger.error(f"MongoDB backup failed: {output}")
                return False
            
            # Create tar archive in container
            tar_cmd = f"tar -cf {container_tar} -C /tmp mongodb_backup"
            exit_code, _ = exec_in_container(self.container, tar_cmd)
            
            if exit_code != 0:
                logger.error(f"Failed to create MongoDB backup archive")
                return False
            
            # Get tar data from container
            cat_cmd = f"cat {container_tar}"
            exit_code, tar_data = exec_in_container(self.container, cat_cmd)
            
            if exit_code != 0 or not tar_data:
                logger.error(f"Failed to retrieve MongoDB backup data")
                return False
            
            # Write to temporary output file first
            temp_output_path = f"{output_path}.tmp"
            with gzip.open(temp_output_path, 'wb') as f:
                f.write(tar_data.encode('utf-8', errors='replace'))
            
            # Atomically move to final location
            shutil.move(temp_output_path, output_path)
            
            logger.info(f"MongoDB backup completed successfully: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error during MongoDB backup: {str(e)}")
            # Attempt to cleanup temporary output file
            if 'temp_output_path' in locals() and os.path.exists(temp_output_path):
                try:
                    os.remove(temp_output_path)
                except Exception:
                    pass
            return False
            
        finally:
            # Clean up in container
            if temp_files:
                cleanup_cmd = f"rm -rf {' '.join(temp_files)}"
                exec_in_container(self.container, cleanup_cmd)
            
            # Clean up local temporary directory
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.warning(f"Failed to clean up temporary directory: {str(e)}")
    
    def _backup_redis(self, output_path: str) -> bool:
        """
        Back up Redis database with improved security and error handling.
        
        Args:
            output_path (str): Path to store backup.
            
        Returns:
            bool: True if successful, False otherwise.
        """
        # Create temporary directory for consistent backups
        temp_dir = None
        temp_files = []
        
        try:
            temp_dir = tempfile.mkdtemp(prefix="redis_backup_")
            
            # Define consistent paths in container
            container_rdb = "/tmp/redis_backup.rdb"
            container_tar = "/tmp/redis_backup.tar"
            
            # Record for cleanup
            temp_files.extend([container_rdb, container_tar])
            
            # Validate credentials
            credentials = {}
            for key in ['password', 'host', 'port']:
                if key in self.credentials and self.credentials[key]:
                    # Validate to prevent injection
                    if not self._validate_credential(key, self.credentials[key]):
                        logger.error(f"Invalid {key} parameter in Redis credentials")
                        return False
                    credentials[key] = self.credentials[key]
            
            # Determine backup approach based on Redis configuration
            # First, check if RDB file is available
            rdb_path = "/data/dump.rdb"
            check_cmd = f"ls {rdb_path} 2>/dev/null || echo 'NOT_FOUND'"
            exit_code, check_output = exec_in_container(self.container, check_cmd)
            
            # Flag to track if we're using RDB file or redis-cli
            using_rdb_file = False
            
            if exit_code == 0 and "NOT_FOUND" not in check_output:
                # RDB file exists, use it for backup
                logger.info("Using existing RDB file for Redis backup")
                using_rdb_file = True
                
                # Validate RDB path
                if not self._validate_path(rdb_path):
                    logger.error(f"Invalid RDB path: {rdb_path}")
                    return False
                
                # Create tar archive of RDB file
                tar_cmd = f"tar -cf {container_tar} {rdb_path}"
                exit_code, _ = exec_in_container(self.container, tar_cmd)
                
                if exit_code != 0:
                    logger.error("Failed to create Redis RDB backup archive")
                    # Fall back to redis-cli approach
                    using_rdb_file = False
            
            # If RDB not available or tar failed, use redis-cli
            if not using_rdb_file:
                logger.info("Using redis-cli for backup")
                
                # Build redis-cli command securely
                cmd_parts = ["redis-cli", f"--rdb {container_rdb}"]
                
                # Add authentication if provided
                # Password will be provided via environment variable
                
                # Add host and port if specified
                if credentials.get('host') and credentials['host'] != 'localhost':
                    cmd_parts.append(f"-h {credentials['host']}")
                
                if credentials.get('port'):
                    cmd_parts.append(f"-p {credentials['port']}")
                
                # Join command parts into a secure command string
                cmd = " ".join(cmd_parts)
                
                # Set up environment variables for sensitive data
                env = {}
                if credentials.get('password'):
                    env["REDIS_PASSWORD"] = credentials['password']
                    # Modified command to use environment variable
                    cmd = f"REDISCLI_AUTH=\"$REDIS_PASSWORD\" {cmd}"
                
                # Execute backup command
                logger.debug(f"Executing Redis backup command (without password)")
                exit_code, output = exec_in_container(self.container, cmd, env)
                
                if exit_code != 0:
                    logger.error(f"Redis backup failed: {output}")
                    return False
                
                # Create tar archive of the generated RDB file
                tar_cmd = f"tar -cf {container_tar} {container_rdb}"
                exit_code, _ = exec_in_container(self.container, tar_cmd)
                
                if exit_code != 0:
                    logger.error("Failed to create Redis backup archive")
                    return False
            
            # Get tar data from container
            cat_cmd = f"cat {container_tar}"
            exit_code, tar_data = exec_in_container(self.container, cat_cmd)
            
            if exit_code != 0 or not tar_data:
                logger.error("Failed to retrieve Redis backup data")
                return False
            
            # Write to temporary output file first
            temp_output_path = f"{output_path}.tmp"
            with gzip.open(temp_output_path, 'wb') as f:
                f.write(tar_data.encode('utf-8', errors='replace'))
            
            # Atomically move to final location
            shutil.move(temp_output_path, output_path)
            
            logger.info(f"Redis backup completed successfully: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error during Redis backup: {str(e)}")
            # Attempt to cleanup temporary output file
            if 'temp_output_path' in locals() and os.path.exists(temp_output_path):
                try:
                    os.remove(temp_output_path)
                except Exception:
                    pass
            return False
            
        finally:
            # Clean up in container
            if temp_files:
                cleanup_cmd = f"rm -rf {' '.join(temp_files)}"
                exec_in_container(self.container, cleanup_cmd)
            
            # Clean up local temporary directory
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.warning(f"Failed to clean up temporary directory: {str(e)}")
    
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
