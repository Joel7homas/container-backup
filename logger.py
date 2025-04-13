#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Logging module for the service-oriented Docker backup system.
Configures logging based on environment variables and provides
consistent logging across all modules.
"""

import os
import sys
import logging
from typing import Dict, Any, Optional

# Global logger cache to prevent duplicate configurations
_loggers = {}

def configure_logging() -> logging.Logger:
    """
    Configure logging based on environment variables.
    
    Environment variables:
        LOG_LEVEL: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        LOG_FORMAT: Custom log format (optional)
    
    Returns:
        logging.Logger: Configured root logger.
    """
    # Get log level from environment variable or default to INFO
    log_level_name = os.environ.get('LOG_LEVEL', 'INFO').upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    
    # Get log format from environment variable or use default
    default_format = '%(asctime)s - %(levelname)s - %(name)s: %(message)s'
    log_format = os.environ.get('LOG_FORMAT', default_format)
    
    # Only configure root logger if not already configured
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        # Configure root logger
        logging.basicConfig(
            level=log_level,
            format=log_format,
            stream=sys.stdout
        )
        root_logger.setLevel(log_level)
    
    return root_logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a configured logger for a module.
    
    Args:
        name (str): Name of the module.
        
    Returns:
        logging.Logger: Configured logger.
    """
    # Check if logger already exists in cache
    if name in _loggers:
        return _loggers[name]
    
    # Ensure root logger is configured
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        configure_logging()
    
    # Create and cache the logger
    logger = logging.getLogger(name)
    _loggers[name] = logger
    
    return logger


def log_startup_info() -> None:
    """
    Log startup configuration information.
    """
    # Get logger directly to avoid circular dependencies
    startup_logger = logging.getLogger("startup")
    
    # Ensure the logger is configured
    if not logging.getLogger().handlers:
        configure_logging()
    
    # Get version from environment variable
    version = os.environ.get('VERSION', 'unknown')
    
    # Log system information
    startup_logger.info("Starting Docker backup system")
    startup_logger.info(f"Version: {version}")
    startup_logger.info(f"Python version: {sys.version}")
    
    # Log environment variables (excluding sensitive data)
    env_vars = _get_safe_environment_variables()
    startup_logger.info(f"Environment variables:")
    for key, value in sorted(env_vars.items()):
        if value:  # Only log non-empty variables
            startup_logger.info(f"  {key}={value}")


def _get_safe_environment_variables() -> Dict[str, str]:
    """
    Get environment variables relevant to the application,
    filtering out sensitive information.
    
    Returns:
        Dict[str, str]: Filtered environment variables.
    """
    # List of variables to include (add more as needed)
    include_vars = [
        'LOG_LEVEL', 
        'LOG_FORMAT',
        'BACKUP_RETENTION_DAYS',
        'CRON_SCHEDULE',
        'EXCLUDE_FROM_BACKUP',
        'PYTHONUNBUFFERED',
        'TZ'
    ]
    
    # List of sensitive variables to mask
    mask_vars = [
        'PORTAINER_API_KEY',
        'PORTAINER_TOKEN',
        'API_KEY',
        'TOKEN',
        'PASSWORD',
        'SECRET'
    ]
    
    env_vars = {}
    
    # Include specific variables
    for var in include_vars:
        if var in os.environ:
            env_vars[var] = os.environ.get(var)
    
    # Include variables with standard prefixes
    for key, value in os.environ.items():
        if key.startswith(('DOCKER_', 'BACKUP_', 'SERVICE_')):
            env_vars[key] = value
    
    # Mask sensitive information
    for key in env_vars:
        for mask_var in mask_vars:
            if mask_var in key:
                env_vars[key] = '********'
                break
    
    return env_vars
