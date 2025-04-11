#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Configuration manager for service-oriented Docker backup system.
Handles loading, merging, and providing service configurations
from various sources with proper precedence.
"""

import os
import json
import logging
from typing import Dict, List, Any, Optional, Union
from pathlib import Path

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

from logger import get_logger

logger = get_logger(__name__)


class ConfigurationManager:
    """Manages service configurations with defaults and overrides."""
    
    def __init__(self, custom_configs: Optional[Dict[str, Any]] = None, config_path: Optional[str] = None):
        """
        Initialize configuration manager.
        
        Args:
            custom_configs (dict, optional): Custom configurations to override defaults.
            config_path (str, optional): Path to configuration file. If not provided, 
                                        will use CONFIG_FILE environment variable.
        """
        self.custom_configs = custom_configs or {}
        self.builtin_configs = self._load_builtin_configs()
        self.env_configs = {}
        self.file_configs = {}
        
        # Load configurations from environment
        self.env_configs = self.load_configs_from_env()
        
        # Initialize default configuration if needed
        self.initialize_configuration(config_path)
        
        # Log configuration sources
        logger.info(f"Loaded {len(self.builtin_configs)} built-in service configurations")
        logger.info(f"Loaded {len(self.env_configs)} service configurations from environment")
        logger.info(f"Loaded {len(self.file_configs)} service configurations from file")
        logger.info(f"Loaded {len(self.custom_configs)} custom service configurations")
    
    def _load_builtin_configs(self) -> Dict[str, Any]:
        """
        Load built-in service configurations.
        
        Returns:
            dict: Dictionary of built-in service configurations.
        """
        configs = {}
        
        # WordPress configuration
        configs["wordpress"] = {
            "database": {
                "type": "mysql",
                "requires_stopping": False,
                "container_patterns": ["*mysql*", "*mariadb*"]
            },
            "files": {
                "data_paths": ["wp-content"],
                "requires_stopping": False,
                "exclusions": ["wp-content/cache/*", "wp-content/debug.log"]
            }
        }
        
        # NextCloud configuration
        configs["nextcloud"] = {
            "database": {
                "type": "postgres",  # or mysql
                "requires_stopping": False,
                "container_patterns": ["*postgres*", "*mysql*", "*mariadb*"]
            },
            "files": {
                "data_paths": ["data", "config", "themes", "apps"],
                "requires_stopping": True,
                "exclusions": ["data/appdata*/cache/*", "data/*/cache/*"]
            }
        }
        
        # Home Assistant configuration
        configs["homeassistant"] = {
            "database": {
                "type": "sqlite",
                "requires_stopping": True,
                "container_patterns": ["*home-assistant*", "*homeassistant*"]
            },
            "files": {
                "data_paths": ["."],
                "requires_stopping": True,
                "exclusions": ["tmp/*", "log/*", "deps/*", "tts/*"]
            }
        }
        
        # Add more built-in configurations here
        
        return configs
    
    def initialize_configuration(self, config_path: Optional[str] = None) -> None:
        """
        Initialize configuration files if they don't exist.
        Creates default configs in the expected locations.
        
        Args:
            config_path (str, optional): Path to the config directory or file.
                If not provided, will use the CONFIG_FILE environment variable.
        """
        # Determine config path - either from parameter or environment variable
        if not config_path:
            config_path = os.environ.get('CONFIG_FILE')
        
        if not config_path:
            logger.warning("No configuration path specified, skipping initialization")
            return
        
        config_path = Path(config_path)
        
        # If it's a directory, use a default filename
        if config_path.is_dir():
            config_path = config_path / "service_configs.json"
        
        # Check if config already exists
        if config_path.exists():
            logger.debug(f"Configuration file already exists at {config_path}")
            try:
                # Load existing configuration
                self.load_configs_from_file(str(config_path))
                return
            except Exception as e:
                logger.warning(f"Failed to load existing configuration: {str(e)}")
        
        # Create directory if it doesn't exist
        os.makedirs(config_path.parent, exist_ok=True)
        
        # Create default configuration
        try:
            default_configs = {
                "wordpress": self.builtin_configs.get("wordpress", {}),
                "nextcloud": self.builtin_configs.get("nextcloud", {}),
                "homeassistant": self.builtin_configs.get("homeassistant", {})
            }
            
            # Add example configuration
            default_configs["example_service"] = {
                "database": {
                    "type": "postgres",
                    "requires_stopping": False,
                    "container_patterns": ["*postgres*", "*db*"]
                },
                "files": {
                    "data_paths": ["/app/data", "/app/config"],
                    "requires_stopping": False,
                    "exclusions": ["*/cache/*", "*/tmp/*", "*.log"]
                },
                "global": {
                    "backup_retention": 14,
                    "exclude_from_backup": False,
                    "priority": 30
                }
            }
            
            # Write to file in appropriate format
            if config_path.suffix.lower() in ['.yaml', '.yml'] and YAML_AVAILABLE:
                with open(config_path, 'w') as f:
                    yaml.dump(default_configs, f, default_flow_style=False, sort_keys=False)
            else:
                # Default to JSON if YAML not available or not specified
                with open(config_path, 'w') as f:
                    json.dump(default_configs, f, indent=2)
            
            logger.info(f"Created default configuration file at {config_path}")
            
            # Load the created configuration
            self.load_configs_from_file(str(config_path))
            
        except Exception as e:
            logger.error(f"Failed to create default configuration file: {str(e)}")


    def _get_default_config(self) -> Dict[str, Any]:
        """
        Get default configuration for services.
        
        Returns:
            dict: Default configuration dictionary.
        """
        return {
            "database": {
                "type": None,
                "requires_stopping": True,
                "container_patterns": [],
                "backup_command": None,
                "credentials": None
            },
            "files": {
                "data_paths": [],
                "requires_stopping": True,
                "exclusions": []
            },
            "global": {
                "backup_retention": 7,  # days
                "exclude_from_backup": False,
                "priority": 50  # 1-100, lower numbers backed up first
            }
        }
    
    def _discover_config(self, containers: List[Any]) -> Dict[str, Any]:
        """
        Discover configuration based on containers.
        
        Args:
            containers (list): List of container objects.
            
        Returns:
            dict: Discovered configuration.
        """
        config = self._get_default_config()
        
        # This is a placeholder implementation
        # Actual container analysis will be implemented
        # when container objects are available from docker_utils
        
        return config
    
    def get_service_config(self, service_name: str, 
                          containers: Optional[List[Any]] = None) -> Dict[str, Any]:
        """
        Get configuration for a service with proper precedence.
        
        Args:
            service_name (str): Name of the service.
            containers (list, optional): List of container objects.
            
        Returns:
            dict: Complete configuration for the service.
        """
        # Start with default config
        config = self._get_default_config()
        
        service_name_lower = service_name.lower()
        
        # Apply built-in config if available
        if service_name_lower in self.builtin_configs:
            logger.debug(f"Applying built-in configuration for {service_name}")
            self._deep_update(config, self.builtin_configs[service_name_lower])
        
        # Apply file config if available
        if service_name_lower in self.file_configs:
            logger.debug(f"Applying file configuration for {service_name}")
            self._deep_update(config, self.file_configs[service_name_lower])
        
        # Apply environment config if available
        if service_name_lower in self.env_configs:
            logger.debug(f"Applying environment configuration for {service_name}")
            self._deep_update(config, self.env_configs[service_name_lower])
        
        # Apply custom config if available
        if service_name in self.custom_configs:
            logger.debug(f"Applying custom configuration for {service_name}")
            self._deep_update(config, self.custom_configs[service_name])
        
        # Apply discovered config if containers provided and no configs found
        if containers and service_name_lower not in (self.builtin_configs.keys() | 
                                                  self.file_configs.keys() | 
                                                  self.env_configs.keys() | 
                                                  self.custom_configs.keys()):
            logger.debug(f"No configuration found for {service_name}, discovering from containers")
            discovered_config = self._discover_config(containers)
            self._deep_update(config, discovered_config)
        
        return config
    
    def load_configs_from_file(self, file_path: str) -> Dict[str, Any]:
        """
        Load configurations from a file.
        
        Args:
            file_path (str): Path to configuration file.
            
        Returns:
            dict: Configurations loaded from file.
        """
        file_path = Path(file_path)
        loaded_configs = {}
        
        if not file_path.exists():
            logger.warning(f"Configuration file not found: {file_path}")
            return loaded_configs
        
        try:
            with open(file_path, 'r') as f:
                if file_path.suffix.lower() in ['.yaml', '.yml']:
                    if not YAML_AVAILABLE:
                        logger.warning("YAML support requires PyYAML. Install with: pip install pyyaml")
                        return loaded_configs
                    loaded_configs = yaml.safe_load(f)
                elif file_path.suffix.lower() == '.json':
                    loaded_configs = json.load(f)
                else:
                    logger.warning(f"Unsupported configuration file format: {file_path.suffix}")
                    return loaded_configs
            
            logger.info(f"Loaded configurations from {file_path}")
            self.file_configs.update(loaded_configs)
        except Exception as e:
            logger.error(f"Error loading configurations from {file_path}: {str(e)}")
        
        return loaded_configs
    
    def load_configs_from_env(self) -> Dict[str, Any]:
        """
        Load configurations from environment variables.
        
        Returns:
            dict: Configurations loaded from environment.
        """
        loaded_configs = {}
        env_config_prefix = "SERVICE_CONFIG_"
        
        for key, value in os.environ.items():
            if key.startswith(env_config_prefix):
                service_name = key[len(env_config_prefix):].lower()
                
                try:
                    config = json.loads(value)
                    loaded_configs[service_name] = config
                    logger.debug(f"Loaded configuration for {service_name} from environment")
                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON in {key} environment variable: {str(e)}")
        
        return loaded_configs
    
    def _deep_update(self, target: Dict[str, Any], source: Dict[str, Any]) -> None:
        """
        Deep update a nested dictionary.
        
        Args:
            target (dict): Target dictionary to update.
            source (dict): Source dictionary with updates.
        """
        for key, value in source.items():
            if isinstance(value, dict) and key in target and isinstance(target[key], dict):
                self._deep_update(target[key], value)
            else:
                target[key] = value
