#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Service discovery module for the service-oriented Docker backup system.
Discovers and categorizes Docker services for backup.
"""

import os
from typing import Dict, List, Any, Optional
from logger import get_logger
from service_backup import ServiceBackup
from utils.docker_utils import get_running_containers

logger = get_logger(__name__)

class ServiceDiscovery:
    """Discovers and categorizes Docker services for backup."""
    
    def __init__(self, portainer_client: Any, config_manager: Any):
        """
        Initialize service discovery.
        
        Args:
            portainer_client (PortainerClient): Portainer client instance.
            config_manager (ConfigurationManager): Configuration manager instance.
        """
        self.portainer_client = portainer_client
        self.config_manager = config_manager
        logger.debug("Initialized service discovery")
    
    def discover_services(self) -> List[ServiceBackup]:
        """
        Discover all services and their components.
        
        Returns:
            list: List of ServiceBackup objects.
        """
        logger.info("Discovering services")
        
        # Get all running containers
        containers = get_running_containers()
        if not containers:
            logger.warning("No running containers found")
            return []
        
        logger.info(f"Found {len(containers)} running containers")
        
        # Get stacks from Portainer
        stacks = self.portainer_client.get_stacks()
        
        # Group containers by service/stack
        services = self._group_by_service(containers, stacks)
        
        # Create ServiceBackup objects
        service_backups = []
        
        for service_name, service_containers in services.items():
            # Skip excluded services
            if self._is_excluded(service_name):
                logger.info(f"Skipping excluded service: {service_name}")
                continue
            
            # Get service configuration
            config = self.config_manager.get_service_config(service_name, service_containers)
            
            # Create ServiceBackup object
            service_backup = ServiceBackup(service_name, service_containers, config)
            service_backups.append(service_backup)
            
            logger.debug(f"Discovered service: {service_name} with {len(service_containers)} containers")
        
        logger.info(f"Discovered {len(service_backups)} services for backup")
        return service_backups
    
    def _group_by_service(self, containers: List[Any], stacks: Dict[str, str]) -> Dict[str, List[Any]]:
        """
        Group containers by service/stack.
        
        Args:
            containers (list): List of container objects.
            stacks (dict): Dictionary of stack names to stack IDs.
            
        Returns:
            dict: Dictionary of service names to container lists.
        """
        services = {}
        
        for container in containers:
            service_name = self._get_service_name(container, stacks)
            
            if service_name not in services:
                services[service_name] = []
            
            services[service_name].append(container)
        
        return services
    
    def _get_service_name(self, container: Any, stacks: Dict[str, str]) -> str:
        """
        Get service name for a container.
        
        Args:
            container (Container): Container object.
            stacks (dict): Dictionary of stack names to stack IDs.
            
        Returns:
            str: Service name.
        """
        # Check for Docker Compose project label
        labels = container.labels if hasattr(container, 'labels') else {}
        
        # Try to get from Docker Compose labels
        if 'com.docker.compose.project' in labels:
            return labels['com.docker.compose.project']
        
        if 'io.docker.compose.project' in labels:
            return labels['io.docker.compose.project']
        
        # Try to get from Portainer labels
        if 'io.portainer.stackname' in labels:
            return labels['io.portainer.stackname']
        
        # Try to get from container name (common prefixes)
        container_name = container.name
        for stack_name in stacks.keys():
            if container_name.startswith(f"{stack_name}_"):
                return stack_name
        
        # Fallback: use container name as service name
        return container_name

    def _is_excluded(self, service_name: str) -> bool:
        """
        Check if a service is excluded from backup.
        
        Args:
            service_name (str): Name of the service.
            
        Returns:
            bool: True if excluded, False otherwise.
        """
        # Get exclusion environment variable
        exclude_env = os.environ.get('EXCLUDE_FROM_BACKUP', '')
        
        # Initialize empty list
        excluded_services = []
        
        # Parse environment variable - handle multiple formats
        if exclude_env:
            # First split by commas (if any)
            comma_items = exclude_env.split(',')
            
            # Then handle space-separated items
            for item in comma_items:
                space_items = item.split()
                excluded_services.extend([s.strip().lower() for s in space_items if s.strip()])
        
        # Log parsed exclusions for debugging
        logger.debug(f"Parsed excluded services: {excluded_services}")
        
        # Check configuration for excluded services
        config = self.config_manager.get_service_config(service_name)
        config_excluded = config.get('global', {}).get('exclude_from_backup', False)
        
        # Check if service name is in excluded list (case-insensitive)
        is_excluded = service_name.lower() in excluded_services or config_excluded
        
        if is_excluded:
            logger.info(f"Service {service_name} is excluded from backup")
        
        return is_excluded
        
