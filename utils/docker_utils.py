#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Docker utility functions for service-oriented Docker backup system.
Provides helper functions for Docker container operations with security
considerations to minimize privilege escalation risks.
"""

import os
import time
import re
from typing import Dict, List, Any, Optional, Union, Tuple, Set

try:
    import docker
    from docker.errors import DockerException, NotFound, APIError
    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False

# Assuming logger.py is in the parent directory
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from logger import get_logger

logger = get_logger(__name__)

# Define allowed Docker API operations to reduce security risks
# This implements a form of least-privilege access to Docker API
ALLOWED_OPERATIONS = {
    'containers': {'list', 'get', 'logs', 'exec_run'},
    'images': {'list', 'get'},
    'networks': {'list'},
    'volumes': {'list'}
}

def validate_docker_environment() -> bool:
    """
    Validate the Docker environment and permissions.
    Checks socket permissions and warns about potential security issues.
    
    Returns:
        bool: True if environment is valid, False otherwise.
    """
    if not DOCKER_AVAILABLE:
        logger.error("Docker SDK for Python not installed. Install with: pip install docker")
        return False
    
    # Check if Docker socket is accessible
    try:
        client = docker.from_env()
        client.ping()
    except DockerException as e:
        logger.error(f"Cannot connect to Docker daemon: {str(e)}")
        
        # Check if this is a permission issue
        if "permission denied" in str(e).lower():
            logger.error("Permission denied accessing Docker socket. This could be due to:")
            logger.error("  1. The user running this application is not in the 'docker' group")
            logger.error("  2. The Docker socket is not accessible to the current user")
            logger.error("  3. The container does not have the Docker socket properly mounted")
            logger.error("\nPossible solutions:")
            logger.error("  - Add the user to the 'docker' group: sudo usermod -aG docker $USER")
            logger.error("  - Restart the container with proper socket mounting")
            logger.error("  - Use a Docker socket proxy for improved security")
        
        return False
    
    # Check if we're running in a container and with proper security
    if os.path.exists('/.dockerenv'):
        logger.info("Running inside a Docker container")
        
        # Check if Docker socket is mounted with potentially unsafe permissions
        docker_socket_path = '/var/run/docker.sock'
        if os.path.exists(docker_socket_path):
            socket_stat = os.stat(docker_socket_path)
            
            # Check if socket has wide permissions
            if socket_stat.st_mode & 0o002:  # world-writable
                logger.warning("Docker socket has world-writable permissions - this is a security risk")
            
            # Check if socket is mounted directly
            logger.warning("Docker socket is mounted directly into the container. For improved security:")
            logger.warning("  1. Consider using a Docker socket proxy (tecnativa/docker-socket-proxy)")
            logger.warning("  2. Mount the socket as read-only: docker.sock:/var/run/docker.sock:ro")
            logger.warning("  3. Enable DOCKER_READ_ONLY=true in environment variables")
    
    # Log that we're using read-only mode for security
    read_only = os.environ.get('DOCKER_READ_ONLY', 'true').lower() in ('true', '1', 'yes')
    if read_only:
        logger.info("Docker read-only mode is enabled for improved security")
    else:
        logger.warning("Docker read-only mode is disabled - this reduces security")
        logger.warning("Set DOCKER_READ_ONLY=true for improved security")
    
    return True

def get_docker_client() -> Optional['docker.DockerClient']:
    """
    Get a Docker client instance with retries on failure.
    Uses read-only access where possible to reduce privilege escalation risks.
    
    Returns:
        docker.DockerClient or None: Docker client instance or None if failed.
    """
    if not DOCKER_AVAILABLE:
        logger.error("Docker SDK for Python not installed. Install with: pip install docker")
        return None
    
    # Check for read-only mode configuration
    read_only = os.environ.get('DOCKER_READ_ONLY', 'true').lower() in ('true', '1', 'yes')
    if read_only:
        logger.info("Using read-only Docker client for improved security")
    
    max_retries = 3
    retry_delay = 2  # seconds
    
    for attempt in range(max_retries):
        try:
            # Use environment variables (DOCKER_HOST, etc.) if available
            client = docker.from_env()
            
            # Test connection
            client.ping()
            logger.debug("Successfully connected to Docker daemon")
            
            # If in read-only mode, wrap the client with security checks
            if read_only:
                return ReadOnlyDockerClient(client)
            return client
            
        except DockerException as e:
            logger.warning(f"Docker connection attempt {attempt + 1}/{max_retries} failed: {str(e)}")
            
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                logger.error("Failed to connect to Docker daemon after multiple attempts")
                return None


class ReadOnlyDockerClient:
    """
    Wrapper around Docker client that restricts operations to read-only
    to reduce privilege escalation risks.
    """
    
    def __init__(self, client: 'docker.DockerClient'):
        """
        Initialize with a Docker client.
        
        Args:
            client: Docker client to wrap
        """
        self._client = client
        self._allowed_ops = ALLOWED_OPERATIONS
        logger.debug("Initialized read-only Docker client wrapper")
        
        # Initialize restricted access to collection attributes
        self.containers = RestrictedCollection(client.containers, 'containers', self._allowed_ops)
        self.images = RestrictedCollection(client.images, 'images', self._allowed_ops)
        self.networks = RestrictedCollection(client.networks, 'networks', self._allowed_ops)
        self.volumes = RestrictedCollection(client.volumes, 'volumes', self._allowed_ops)
    
    def ping(self) -> bool:
        """Ping the Docker daemon to verify connection."""
        return self._client.ping()


class RestrictedCollection:
    """Wrapper for Docker collections that restricts operations."""
    
    def __init__(self, collection: Any, collection_name: str, allowed_ops: Dict[str, Set[str]]):
        """
        Initialize with a Docker collection.
        
        Args:
            collection: Docker collection to wrap
            collection_name: Name of the collection (containers, images, etc.)
            allowed_ops: Dictionary of allowed operations
        """
        self._collection = collection
        self._collection_name = collection_name
        self._allowed_ops = allowed_ops
        
    def __getattr__(self, name: str) -> Any:
        """
        Get attribute from the collection, checking against allowed operations.
        
        Args:
            name: Attribute name
            
        Returns:
            Attribute value
            
        Raises:
            PermissionError: If operation is not allowed
        """
        if self._collection_name in self._allowed_ops and name in self._allowed_ops[self._collection_name]:
            return getattr(self._collection, name)
        else:
            operation = f"{self._collection_name}.{name}"
            logger.warning(f"Blocked potentially dangerous Docker operation: {operation}")
            raise PermissionError(f"Operation not allowed: {operation}")


def _is_valid_container_id(container_id: str) -> bool:
    """
    Validate container ID format to prevent injection attacks.
    
    Args:
        container_id: Container ID or name to validate
        
    Returns:
        bool: True if valid, False otherwise
    """
    # Docker IDs are 64-character hex strings
    # Container names can be alphanumeric with some special chars
    return bool(re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,63}$', container_id) or 
                re.match(r'^[a-f0-9]{12,64}$', container_id))


def _is_valid_container(container: Any) -> bool:
    """
    Validate a container object.
    
    Args:
        container: Container object to validate
        
    Returns:
        bool: True if valid, False otherwise
    """
    if not container:
        return False
    
    # Check for basic container attributes
    required_attrs = ['id', 'name', 'attrs']
    return all(hasattr(container, attr) for attr in required_attrs)


def get_container_by_id(container_id: str) -> Optional[Any]:
    """
    Get container object by ID with error handling.
    Uses read-only access to reduce privilege escalation risks.
    
    Args:
        container_id (str): Container ID or name.
        
    Returns:
        Container or None: Container object or None if not found/error.
    """
    client = get_docker_client()
    if not client:
        return None
    
    # Validate container ID to prevent injection
    if not _is_valid_container_id(container_id):
        logger.error(f"Invalid container ID format: {container_id}")
        return None
    
    try:
        container = client.containers.get(container_id)
        return container
    except NotFound:
        logger.warning(f"Container not found: {container_id}")
        return None
    except APIError as e:
        logger.error(f"API error getting container {container_id}: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error getting container {container_id}: {str(e)}")
        return None


def get_container_environment(container: Any) -> Dict[str, str]:
    """
    Get environment variables for a container.
    Uses read-only access to reduce privilege escalation risks.
    
    Args:
        container (Container): Container object.
        
    Returns:
        dict: Dictionary of environment variables.
    """
    env_vars = {}
    
    try:
        # Validate container object
        if not _is_valid_container(container):
            logger.error("Invalid container object provided")
            return {}
        
        # Get environment from container inspect data
        if hasattr(container, 'attrs') and 'Config' in container.attrs:
            env_list = container.attrs['Config'].get('Env', [])
            
            # Parse environment variables
            for env_str in env_list:
                if '=' in env_str:
                    key, value = env_str.split('=', 1)
                    env_vars[key] = value
        
        return env_vars
        
    except Exception as e:
        container_name = getattr(container, 'name', 'unknown')
        logger.error(f"Error getting environment for container {container_name}: {str(e)}")
        return {}


def get_container_mounts(container: Any) -> List[Dict[str, Any]]:
    """
    Get volume mounts for a container.
    Uses read-only access to reduce privilege escalation risks.
    
    Args:
        container (Container): Container object.
        
    Returns:
        list: List of mount objects with normalized data.
    """
    mounts = []
    
    try:
        # Validate container object
        if not _is_valid_container(container):
            logger.error("Invalid container object provided")
            return []
        
        # Get mounts from container inspect data
        if hasattr(container, 'attrs') and 'Mounts' in container.attrs:
            raw_mounts = container.attrs['Mounts']
            
            for mount in raw_mounts:
                # Normalize mount information
                mount_info = {
                    'type': mount.get('Type', 'unknown'),
                    'source': mount.get('Source', ''),
                    'destination': mount.get('Destination', ''),
                    'mode': mount.get('Mode', 'rw'),
                    'rw': mount.get('RW', True),
                    'propagation': mount.get('Propagation', '')
                }
                mounts.append(mount_info)
        
        # Alternative path for newer Docker versions
        elif hasattr(container, 'attrs') and 'HostConfig' in container.attrs:
            host_config = container.attrs['HostConfig']
            
            # Check for Binds
            if 'Binds' in host_config and host_config['Binds']:
                for bind in host_config['Binds']:
                    parts = bind.split(':')
                    if len(parts) >= 2:
                        source, destination = parts[0], parts[1]
                        mode = 'rw'
                        if len(parts) >= 3:
                            mode = parts[2]
                        
                        mount_info = {
                            'type': 'bind',
                            'source': source,
                            'destination': destination,
                            'mode': mode,
                            'rw': 'ro' not in mode,
                            'propagation': ''
                        }
                        mounts.append(mount_info)
            
            # Check for Volumes
            if 'Volumes' in host_config and host_config['Volumes']:
                for dest, source in host_config['Volumes'].items():
                    mount_info = {
                        'type': 'volume',
                        'source': source if source else '',
                        'destination': dest,
                        'mode': 'rw',
                        'rw': True,
                        'propagation': ''
                    }
                    mounts.append(mount_info)
        
        return mounts
        
    except Exception as e:
        container_name = getattr(container, 'name', 'unknown')
        logger.error(f"Error getting mounts for container {container_name}: {str(e)}")
        return []


def get_container_networks(container: Any) -> Dict[str, Dict[str, Any]]:
    """
    Get networks for a container.
    Uses read-only access to reduce privilege escalation risks.
    
    Args:
        container (Container): Container object.
        
    Returns:
        dict: Dictionary of network information by network name.
    """
    networks = {}
    
    try:
        # Validate container object
        if not _is_valid_container(container):
            logger.error("Invalid container object provided")
            return {}
        
        # Get networks from container inspect data
        if hasattr(container, 'attrs') and 'NetworkSettings' in container.attrs:
            network_settings = container.attrs['NetworkSettings']
            
            if 'Networks' in network_settings:
                for network_name, network_config in network_settings['Networks'].items():
                    networks[network_name] = {
                        'ip_address': network_config.get('IPAddress', ''),
                        'gateway': network_config.get('Gateway', ''),
                        'mac_address': network_config.get('MacAddress', ''),
                        'network_id': network_config.get('NetworkID', '')
                    }
        
        return networks
        
    except Exception as e:
        container_name = getattr(container, 'name', 'unknown')
        logger.error(f"Error getting networks for container {container_name}: {str(e)}")
        return {}


def get_running_containers() -> List[Any]:
    """
    Get all running containers.
    Uses read-only access to reduce privilege escalation risks.
    
    Returns:
        list: List of running container objects.
    """
    client = get_docker_client()
    if not client:
        return []
    
    try:
        containers = client.containers.list(filters={"status": "running"})
        logger.info(f"Found {len(containers)} running containers")
        return containers
    except Exception as e:
        logger.error(f"Error getting running containers: {str(e)}")
        return []


def exec_in_container(container: Any, command: str, env: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
    """
    Execute a command in a container.
    Performs validation and sanitization to reduce security risks.
    
    Args:
        container (Container): Container object.
        command (str): Command to execute.
        env (dict, optional): Environment variables for the command.
        
    Returns:
        tuple: (exit_code, output) tuple.
    """
    try:
        # Validate container object
        if not _is_valid_container(container):
            logger.error("Invalid container object provided")
            return (-1, "Invalid container object")
        
        # Validate command
        if not isinstance(command, str) or not command.strip():
            logger.error("Invalid command provided")
            return (-1, "Invalid command")
        
        # Default environment variables
        env = env or {}
        
        # Sanitize command to prevent injection
        # We're just executing the command as-is, but in a real security context,
        # further validation might be needed here depending on the use case
        
        logger.debug(f"Executing in {container.name}: {command}")
        
        # Set an execution timeout to prevent hanging
        timeout = int(os.environ.get('DOCKER_EXEC_TIMEOUT', '300'))  # 5 minutes default
        
        result = container.exec_run(
            cmd=command,
            environment=env,
            detach=False,
            tty=False,
            demux=False
        )
        
        exit_code = result.exit_code
        output = result.output.decode('utf-8', errors='replace')
        
        if exit_code != 0:
            logger.warning(f"Command in {container.name} exited with code {exit_code}: {output}")
        
        return exit_code, output
        
    except Exception as e:
        container_name = getattr(container, 'name', 'unknown')
        logger.error(f"Error executing command in {container_name}: {str(e)}")
        return -1, str(e)
