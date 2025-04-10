#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Docker utility functions for service-oriented Docker backup system.
Provides helper functions for Docker container operations.
"""

import os
import time
from typing import Dict, List, Any, Optional, Union, Tuple

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


def get_docker_client() -> Optional['docker.DockerClient']:
    """
    Get a Docker client instance with retries on failure.
    
    Returns:
        docker.DockerClient or None: Docker client instance or None if failed.
    """
    if not DOCKER_AVAILABLE:
        logger.error("Docker SDK for Python not installed. Install with: pip install docker")
        return None
    
    max_retries = 3
    retry_delay = 2  # seconds
    
    for attempt in range(max_retries):
        try:
            # Use environment variables (DOCKER_HOST, etc.) if available
            client = docker.from_env()
            
            # Test connection
            client.ping()
            logger.debug("Successfully connected to Docker daemon")
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


def get_container_by_id(container_id: str) -> Optional[Any]:
    """
    Get container object by ID with error handling.
    
    Args:
        container_id (str): Container ID or name.
        
    Returns:
        Container or None: Container object or None if not found/error.
    """
    client = get_docker_client()
    if not client:
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
    
    Args:
        container (Container): Container object.
        
    Returns:
        dict: Dictionary of environment variables.
    """
    env_vars = {}
    
    try:
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
        logger.error(f"Error getting environment for container {container.name}: {str(e)}")
        return {}


def get_container_mounts(container: Any) -> List[Dict[str, Any]]:
    """
    Get volume mounts for a container.
    
    Args:
        container (Container): Container object.
        
    Returns:
        list: List of mount objects with normalized data.
    """
    mounts = []
    
    try:
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
        logger.error(f"Error getting mounts for container {container.name}: {str(e)}")
        return []


def get_container_networks(container: Any) -> Dict[str, Dict[str, Any]]:
    """
    Get networks for a container.
    
    Args:
        container (Container): Container object.
        
    Returns:
        dict: Dictionary of network information by network name.
    """
    networks = {}
    
    try:
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
        logger.error(f"Error getting networks for container {container.name}: {str(e)}")
        return {}


def get_running_containers() -> List[Any]:
    """
    Get all running containers.
    
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
    
    Args:
        container (Container): Container object.
        command (str): Command to execute.
        env (dict, optional): Environment variables for the command.
        
    Returns:
        tuple: (exit_code, output) tuple.
    """
    try:
        env = env or {}
        logger.debug(f"Executing in {container.name}: {command}")
        
        result = container.exec_run(
            cmd=command,
            environment=env,
            detach=False,
            tty=False
        )
        
        exit_code = result.exit_code
        output = result.output.decode('utf-8', errors='replace')
        
        if exit_code != 0:
            logger.warning(f"Command in {container.name} exited with code {exit_code}: {output}")
        
        return exit_code, output
        
    except Exception as e:
        logger.error(f"Error executing command in {container.name}: {str(e)}")
        return -1, str(e)
