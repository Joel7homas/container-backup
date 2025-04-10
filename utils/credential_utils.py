#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Credential utilities for service-oriented Docker backup system.
Provides functions for credential management and parsing.
"""

import re
import copy
from typing import Dict, List, Any, Optional, Union
from urllib.parse import urlparse, parse_qs

# Assuming logger.py is in the parent directory
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from logger import get_logger

logger = get_logger(__name__)


def parse_database_url(url: str) -> Optional[Dict[str, str]]:
    """
    Parse a database connection URL.
    
    Supports various URL formats:
    - postgres://user:pass@host:port/dbname
    - mysql://user:pass@host:port/dbname
    - postgresql://user:pass@host:port/dbname?options
    
    Args:
        url (str): Database connection URL.
        
    Returns:
        dict or None: Dictionary of connection parameters or None if invalid.
    """
    if not url:
        return None
    
    try:
        # Parse URL
        parsed = urlparse(url)
        
        # Extract components
        scheme = parsed.scheme.lower()
        username = parsed.username
        password = parsed.password
        hostname = parsed.hostname
        port = parsed.port
        
        # Extract database name from path
        path = parsed.path.lstrip('/')
        database = path if path else None
        
        # Extract query parameters
        params = {}
        if parsed.query:
            query_params = parse_qs(parsed.query)
            for key, values in query_params.items():
                params[key] = values[0] if values else None
        
        # Determine database type
        db_type = None
        if scheme in ('postgres', 'postgresql'):
            db_type = 'postgres'
        elif scheme in ('mysql', 'mariadb'):
            db_type = 'mysql'
        elif scheme == 'sqlite':
            db_type = 'sqlite'
        elif scheme in ('mongodb', 'mongo'):
            db_type = 'mongodb'
        
        return {
            'type': db_type,
            'user': username,
            'password': password,
            'host': hostname,
            'port': port,
            'database': database,
            'params': params
        }
    
    except Exception as e:
        logger.error(f"Error parsing database URL: {str(e)}")
        return None


def find_credential_in_env(env_vars: Dict[str, str], possible_keys: List[str]) -> Optional[str]:
    """
    Find a credential in environment variables.
    
    Args:
        env_vars (dict): Dictionary of environment variables.
        possible_keys (list): List of possible key names.
        
    Returns:
        str or None: Found credential or None if not found.
    """
    if not env_vars or not possible_keys:
        return None
    
    # Try each key in order
    for key in possible_keys:
        if key in env_vars and env_vars[key]:
            value = env_vars[key]
            
            # Handle variable references
            if isinstance(value, str) and value.startswith('$'):
                value = resolve_env_var(value, env_vars)
            
            return value
    
    return None


def resolve_env_var(value: str, env_vars: Dict[str, str]) -> str:
    """
    Resolve a single environment variable reference.
    
    Args:
        value (str): Value possibly containing a reference.
        env_vars (dict): Dictionary of environment variables.
        
    Returns:
        str: Resolved value.
    """
    if not isinstance(value, str) or not value.startswith('$'):
        return value
    
    # Handle ${VAR} format
    if value.startswith('${') and value.endswith('}'):
        var_name = value[2:-1]
        if var_name in env_vars:
            return env_vars[var_name]
    
    # Handle $VAR format
    elif value.startswith('$'):
        var_name = value[1:]
        if var_name in env_vars:
            return env_vars[var_name]
    
    # Return original if not resolved
    return value


def resolve_env_var_references(env_vars: Dict[str, str]) -> Dict[str, str]:
    """
    Resolve variable references in environment variables.
    
    Args:
        env_vars (dict): Dictionary of environment variables.
        
    Returns:
        dict: Dictionary with resolved environment variables.
    """
    if not env_vars:
        return {}
    
    resolved = copy.deepcopy(env_vars)
    
    # Multiple passes to handle nested references
    for _ in range(3):  # Limit to prevent infinite loops
        any_resolved = False
        
        for key, value in resolved.items():
            if isinstance(value, str):
                new_value = resolve_env_var(value, resolved)
                if new_value != value:
                    resolved[key] = new_value
                    any_resolved = True
        
        # If no variables were resolved in this pass, we're done
        if not any_resolved:
            break
    
    return resolved


def extract_database_credentials(env_vars: Dict[str, str], 
                              db_type: str, 
                              stack_name: Optional[str] = None) -> Dict[str, str]:
    """
    Extract database credentials from environment variables.
    
    Args:
        env_vars (dict): Dictionary of environment variables.
        db_type (str): Type of database (postgres, mysql, sqlite, etc.).
        stack_name (str, optional): Name of the stack for specialized keys.
        
    Returns:
        dict: Dictionary of database credentials.
    """
    credentials = {
        'user': None,
        'password': None,
        'database': None,
        'host': None,
        'port': None
    }
    
    # Resolve variable references
    env_vars = resolve_env_var_references(env_vars)
    
    # Create stack-specific keys
    stack_upper = stack_name.upper() if stack_name else ''
    
    # First check for connection URLs
    connection_keys = [
        'DATABASE_URL',
        'DB_URI',
        'POSTGRES_URI',
        'MYSQL_URI',
        f'{stack_upper}_DATABASE_URL',
        f'{db_type.upper()}_URI',
        f'{stack_upper}_DB_URI'
    ]
    
    conn_url = find_credential_in_env(env_vars, connection_keys)
    if conn_url:
        parsed = parse_database_url(conn_url)
        if parsed:
            credentials.update({
                'user': parsed.get('user'),
                'password': parsed.get('password'),
                'database': parsed.get('database'),
                'host': parsed.get('host'),
                'port': parsed.get('port')
            })
    
    # Check for specific credential variables
    if db_type == 'postgres':
        # User
        user_keys = [
            'DB_USER', 'POSTGRES_USER', 'PGUSER',
            'DATABASE_USER', 'POSTGRESQL_USER',
            f'{stack_upper}_DB_USER',
            'DB_USERNAME',
            f'{stack_upper}_DBUSER',
            'POSTGRES_NON_ROOT_USER'
        ]
        credentials['user'] = find_credential_in_env(env_vars, user_keys) or credentials['user']
        
        # Password
        password_keys = [
            'DB_PASSWORD', 'POSTGRES_PASSWORD', 'PGPASSWORD',
            'DATABASE_PASSWORD', 'POSTGRESQL_PASSWORD',
            f'{stack_upper}_DB_PASSWORD',
            f'{stack_upper}_DBPASS',
            'POSTGRES_NON_ROOT_PASSWORD'
        ]
        credentials['password'] = find_credential_in_env(env_vars, password_keys) or credentials['password']
        
        # Database
        database_keys = [
            'DB_NAME', 'POSTGRES_DB', 'DB_DATABASE',
            'DATABASE_NAME', 'POSTGRESQL_DATABASE',
            f'{stack_upper}_DB_NAME',
            'DB_DATABASE_NAME',
            f'{stack_upper}_DBNAME'
        ]
        credentials['database'] = find_credential_in_env(env_vars, database_keys) or credentials['database']
    
    elif db_type in ['mysql', 'mariadb']:
        # For MySQL/MariaDB, try to use root credentials
        credentials['user'] = 'root'
        
        # Root password
        password_keys = [
            'MYSQL_ROOT_PASSWORD',
            'DB_ROOT_PASSWD',
            f'INIT_{stack_upper}_MYSQL_ROOT_PASSWORD',
            'MARIADB_ROOT_PASSWORD'
        ]
        credentials['password'] = find_credential_in_env(env_vars, password_keys) or credentials['password']
        
        # If root password not found, try regular user credentials
        if not credentials['password']:
            user_keys = [
                'DB_USER', 'MYSQL_USER', 'DATABASE_USER',
                f'{stack_upper}_DB_USER',
                'MARIADB_USER'
            ]
            credentials['user'] = find_credential_in_env(env_vars, user_keys) or credentials['user']
            
            password_keys = [
                'DB_PASSWORD', 'MYSQL_PASSWORD', 'DATABASE_PASSWORD',
                f'{stack_upper}_DB_PASSWORD',
                'MARIADB_PASSWORD'
            ]
            credentials['password'] = find_credential_in_env(env_vars, password_keys) or credentials['password']
        
        # Database
        database_keys = [
            'DB_NAME', 'MYSQL_DATABASE', 'DB_DATABASE',
            'DATABASE_NAME', 'MARIADB_DATABASE',
            f'{stack_upper}_DB_NAME',
            f'{stack_upper}_MYSQL_DB_NAME'
        ]
        credentials['database'] = find_credential_in_env(env_vars, database_keys) or credentials['database']
    
    # Host and port are common across database types
    host_keys = [
        'DB_HOST', f'{db_type.upper()}_HOST', 'DATABASE_HOST',
        f'{stack_upper}_DB_HOST'
    ]
    credentials['host'] = find_credential_in_env(env_vars, host_keys) or credentials['host'] or 'localhost'
    
    port_keys = [
        'DB_PORT', f'{db_type.upper()}_PORT', 'DATABASE_PORT',
        f'{stack_upper}_DB_PORT'
    ]
    port_value = find_credential_in_env(env_vars, port_keys)
    if port_value:
        try:
            credentials['port'] = int(port_value)
        except ValueError:
            pass
    
    return credentials


def mask_sensitive_data(data: Any) -> Any:
    """
    Mask sensitive data for logging.
    
    Args:
        data (any): Data to mask.
        
    Returns:
        any: Masked data.
    """
    if isinstance(data, dict):
        masked = {}
        for key, value in data.items():
            # Mask sensitive keys
            if any(sensitive in key.lower() for sensitive in 
                  ['password', 'secret', 'token', 'key', 'pass', 'auth']):
                if value and isinstance(value, str):
                    masked[key] = '********'
                else:
                    masked[key] = value
            else:
                masked[key] = mask_sensitive_data(value)
        return masked
    elif isinstance(data, list):
        return [mask_sensitive_data(item) for item in data]
    elif isinstance(data, str):
        # Check if the string looks like a password or token
        if len(data) > 8 and any(c.isdigit() for c in data) and any(c.isalpha() for c in data):
            # Check if it matches common secret patterns
            if re.search(r'(password|secret|token|key|pass|auth)', data, re.IGNORECASE):
                return '********'
        return data
    else:
        return data
