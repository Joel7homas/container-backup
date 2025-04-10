#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Portainer client for service-oriented Docker backup system.
Handles communication with Portainer API to retrieve stack information and credentials.
"""

import os
import time
import requests
from typing import Dict, List, Any, Optional, Union, Tuple
from urllib3.exceptions import InsecureRequestWarning
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from logger import get_logger

# Suppress insecure request warnings when verify=False is used
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

logger = get_logger(__name__)


class PortainerClient:
    """Client for interacting with Portainer API."""
    
    def __init__(self, url: str, api_key: str):
        """
        Initialize Portainer client.
        
        Args:
            url (str): Portainer URL.
            api_key (str): Portainer API key.
        """
        self.url = url.rstrip('/')
        self.api_key = api_key
        self.session = self._create_session()
        self.verify_ssl = not os.environ.get('PORTAINER_INSECURE', '').lower() in ('true', '1', 'yes')
        
        # Get timeouts from environment variables or use defaults
        self.connect_timeout = int(os.environ.get('PORTAINER_CONNECT_TIMEOUT', '5'))
        self.read_timeout = int(os.environ.get('PORTAINER_READ_TIMEOUT', '30'))
        self.retry_total = int(os.environ.get('PORTAINER_RETRY_TOTAL', '3'))
        self.retry_backoff = float(os.environ.get('PORTAINER_RETRY_BACKOFF', '0.5'))
        
        if not self.verify_ssl:
            logger.warning("SSL verification is disabled for Portainer API requests")
        
        logger.info(f"Initialized Portainer client for {self.url} "
                   f"(connect_timeout={self.connect_timeout}s, read_timeout={self.read_timeout}s, "
                   f"retries={self.retry_total})")
        
        # Cache for API responses
        self._cache = {}
        self._cache_ttl = int(os.environ.get('PORTAINER_CACHE_TTL', '300'))  # 5 minutes default
    
    def _create_session(self) -> requests.Session:
        """
        Create a requests session with retry capabilities.
        
        Returns:
            requests.Session: Configured session object.
        """
        session = requests.Session()
        
        # Set up headers
        session.headers.update({
            "X-API-Key": self.api_key,
            "Content-Type": "application/json"
        })
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=self.retry_total,
            backoff_factor=self.retry_backoff,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"]
        )
        
        # Mount the adapter to the session
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session
    
    def _make_request(self, method: str, endpoint: str, 
                    params: Optional[Dict[str, Any]] = None,
                    data: Optional[Dict[str, Any]] = None,
                    cache: bool = False,
                    custom_timeout: Optional[Tuple[int, int]] = None) -> Optional[Dict[str, Any]]:
        """
        Make a request to the Portainer API with retries.
        
        Args:
            method (str): HTTP method (GET, POST, etc.)
            endpoint (str): API endpoint.
            params (dict, optional): Query parameters.
            data (dict, optional): Request body.
            cache (bool): Whether to cache the response.
            custom_timeout (tuple, optional): Custom timeout as (connect_timeout, read_timeout).
        
        Returns:
            dict or None: Response data or None if failed.
        """
        url = f"{self.url}{endpoint}"
        
        # Use custom timeout if provided, otherwise use defaults
        timeout = custom_timeout or (self.connect_timeout, self.read_timeout)
        
        # Check cache for GET requests
        cache_key = f"{method}:{endpoint}:{str(params)}"
        if method == "GET" and cache and cache_key in self._cache:
            cache_entry = self._cache[cache_key]
            if time.time() - cache_entry["timestamp"] < self._cache_ttl:
                logger.debug(f"Using cached response for {endpoint}")
                return cache_entry["data"]
        
        # Custom retry handling for non-retryable errors
        manual_retries = 2  # Manual retries for connection errors
        retry_delay = self.retry_backoff
        
        for manual_attempt in range(manual_retries + 1):
            try:
                logger.debug(f"Making {method} request to {endpoint} (timeout={timeout}s)")
                
                # Add request timeout monitoring
                start_time = time.time()
                
                response = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=data,
                    verify=self.verify_ssl,
                    timeout=timeout
                )
                
                # Log request duration for monitoring
                duration = time.time() - start_time
                logger.debug(f"Request to {endpoint} completed in {duration:.2f}s")
                
                # Check for slow requests
                if duration > timeout[1] * 0.8:  # If took more than 80% of timeout
                    logger.warning(f"Slow request to {endpoint}: {duration:.2f}s "
                                  f"(close to timeout of {timeout[1]}s)")
                
                response.raise_for_status()
                
                # Parse response
                if response.content:
                    result = response.json()
                else:
                    result = {}
                
                # Cache successful GET responses if requested
                if method == "GET" and cache:
                    self._cache[cache_key] = {
                        "timestamp": time.time(),
                        "data": result
                    }
                
                return result
            
            except requests.exceptions.ConnectTimeout as e:
                logger.warning(f"Connection timeout on {endpoint}: {str(e)}")
                if manual_attempt < manual_retries:
                    delay = retry_delay * (2 ** manual_attempt)
                    logger.info(f"Retrying in {delay}s (attempt {manual_attempt+1}/{manual_retries})")
                    time.sleep(delay)
                else:
                    logger.error(f"Failed to connect to {endpoint} after {manual_retries+1} attempts")
                    return None
            
            except requests.exceptions.ReadTimeout as e:
                logger.warning(f"Read timeout on {endpoint}: {str(e)}")
                if manual_attempt < manual_retries:
                    delay = retry_delay * (2 ** manual_attempt)
                    logger.info(f"Retrying in {delay}s (attempt {manual_attempt+1}/{manual_retries})")
                    time.sleep(delay)
                else:
                    logger.error(f"Request to {endpoint} timed out after {manual_retries+1} attempts")
                    return None
            
            except requests.exceptions.RequestException as e:
                # Other request errors will be handled by the retry adapter
                logger.warning(f"Request to {endpoint} failed: {str(e)}")
                if manual_attempt < manual_retries:
                    delay = retry_delay * (2 ** manual_attempt)
                    logger.info(f"Retrying in {delay}s (attempt {manual_attempt+1}/{manual_retries})")
                    time.sleep(delay)
                else:
                    logger.error(f"Failed to make request to {endpoint} after multiple attempts")
                    return None
            
            except ValueError as e:
                logger.error(f"JSON parsing error for {endpoint}: {str(e)}")
                return None
    
    def get_stacks(self) -> Dict[str, str]:
        """
        Get all stacks from Portainer.
        
        Returns:
            dict: Dictionary of stack names to stack IDs.
        """
        # Use a longer timeout for potentially large response
        result = self._make_request("GET", "/api/stacks", 
                                 cache=True, 
                                 custom_timeout=(self.connect_timeout, self.read_timeout * 2))
        
        if not result:
            logger.error("Failed to get stacks from Portainer")
            return {}
        
        stacks = {}
        try:
            for stack in result:
                stack_name = stack.get('Name', '')
                stack_id = stack.get('Id', 0)
                if stack_name and stack_id:
                    stacks[stack_name] = str(stack_id)
            
            logger.info(f"Retrieved {len(stacks)} stacks from Portainer")
            return stacks
            
        except Exception as e:
            logger.error(f"Error processing stacks response: {str(e)}")
            return {}
    
    def get_stack_env(self, stack_name: str, stacks_dict: Dict[str, str]) -> Optional[Dict[str, str]]:
        """
        Get environment variables for a stack.
        
        Args:
            stack_name (str): Name of the stack.
            stacks_dict (dict): Dictionary of stack names to stack IDs.
            
        Returns:
            dict or None: Dictionary of environment variables or None if not found.
        """
        if not stack_name or stack_name not in stacks_dict:
            logger.warning(f"Stack not found: {stack_name}")
            return None
        
        stack_id = stacks_dict[stack_name]
        stack_details = self.get_stack_details(stack_id)
        
        if not stack_details:
            logger.error(f"Failed to get details for stack: {stack_name}")
            return None
        
        env_vars = {}
        
        try:
            # Process environment variables from stack details
            if 'Env' in stack_details:
                # Handle both list of dicts and list of strings formats
                for env in stack_details['Env']:
                    if isinstance(env, dict) and 'name' in env and 'value' in env:
                        env_vars[env['name']] = env['value']
                    elif isinstance(env, str) and '=' in env:
                        key, value = env.split('=', 1)
                        env_vars[key] = value
            
            # Resolve variable references
            resolved_vars = env_vars.copy()
            for key, value in env_vars.items():
                if isinstance(value, str):
                    # Handle ${VAR} style references
                    if value.startswith('${') and value.endswith('}'):
                        ref_var = value[2:-1]
                        if ref_var in env_vars:
                            resolved_vars[key] = env_vars[ref_var]
                    
                    # Handle $VAR style references
                    elif value.startswith('$') and len(value) > 1:
                        ref_var = value[1:]
                        if ref_var in env_vars:
                            resolved_vars[key] = env_vars[ref_var]
            
            logger.debug(f"Retrieved {len(resolved_vars)} environment variables for stack: {stack_name}")
            return resolved_vars
            
        except Exception as e:
            logger.error(f"Error processing environment variables for stack {stack_name}: {str(e)}")
            return {}
    
    def get_stack_by_container(self, container_id: str) -> Optional[Dict[str, Any]]:
        """
        Get stack information for a container.
        
        Args:
            container_id (str): Container ID.
            
        Returns:
            dict or None: Stack information or None if not found.
        """
        # Get all stacks
        stacks_dict = self.get_stacks()
        if not stacks_dict:
            return None
        
        try:
            # For each stack, check if it contains the container
            for stack_name, stack_id in stacks_dict.items():
                stack_details = self.get_stack_details(stack_id)
                
                if not stack_details:
                    continue
                
                # Check if this stack has container information
                # This depends on Portainer API version and stack type
                if 'Containers' in stack_details:
                    for container in stack_details['Containers']:
                        if container.get('Id') == container_id:
                            logger.debug(f"Container {container_id} found in stack {stack_name}")
                            return {
                                'name': stack_name,
                                'id': stack_id,
                                'details': stack_details
                            }
            
            logger.warning(f"No stack found for container: {container_id}")
            return None
            
        except Exception as e:
            logger.error(f"Error finding stack for container {container_id}: {str(e)}")
            return None
    
    def get_stack_details(self, stack_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed stack information.
        
        Args:
            stack_id (str): Stack ID.
            
        Returns:
            dict or None: Detailed stack information or None if not found.
        """
        endpoint = f"/api/stacks/{stack_id}"
        result = self._make_request("GET", endpoint, cache=True)
        
        if not result:
            logger.warning(f"Failed to get details for stack ID: {stack_id}")
            return None
        
        return result
    
    def clear_cache(self) -> None:
        """
        Clear the API response cache.
        Useful when forced refresh is needed.
        """
        cache_size = len(self._cache)
        self._cache.clear()
        logger.info(f"Cleared Portainer API cache ({cache_size} entries)")
    
    def check_connection(self) -> bool:
        """
        Check if connection to Portainer API is working.
        
        Returns:
            bool: True if connection is working, False otherwise.
        """
        try:
            # Use a short timeout for this check
            result = self._make_request("GET", "/api/status", 
                                     cache=False, 
                                     custom_timeout=(2, 5))
            
            if result:
                logger.info(f"Successfully connected to Portainer API")
                return True
            else:
                logger.error(f"Failed to connect to Portainer API")
                return False
                
        except Exception as e:
            logger.error(f"Error checking Portainer API connection: {str(e)}")
            return False
