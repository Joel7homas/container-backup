#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Main entry point for service-oriented Docker backup system.
Coordinates initialization, command-line interface, and scheduling.
"""

import os
import sys
import json
import time
import signal
import argparse
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Union, Tuple

# Import our modules
from logger import configure_logging, get_logger
from config_manager import ConfigurationManager
from portainer_client import PortainerClient
from backup_manager import BackupManager
from utils.docker_utils import validate_docker_environment

try:
    import schedule
    SCHEDULE_AVAILABLE = True
except ImportError:
    SCHEDULE_AVAILABLE = False

# Global variables for signal handling
shutdown_event = threading.Event()
logger = None


def initialize_components() -> Tuple[PortainerClient, ConfigurationManager, BackupManager]:
    """
    Set up all system components.
    
    Returns:
        tuple: Tuple of initialized components (portainer_client, config_manager, backup_manager).
    """
    global logger
    
    # Configure logging
    configure_logging()
    logger = get_logger(__name__)
    
    logger.info("Initializing Docker backup system")
    
    # Check required environment variables
    required_vars = ['PORTAINER_URL', 'PORTAINER_API_KEY']
    missing_vars = [var for var in required_vars if var not in os.environ]
    
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        sys.exit(1)

    if not validate_docker_environment():
        logger.warning("Docker environment validation raised warnings")
    
    # Initialize portainer client
    portainer_client = PortainerClient(
        os.environ['PORTAINER_URL'],
        os.environ['PORTAINER_API_KEY']
    )
    
    # Initialize configuration manager
    config_path = os.environ.get('CONFIG_FILE', '/app/config/service_configs.json')
    config_manager = ConfigurationManager(config_path=config_path)

    
    if config_path:
        try:
            config_manager.load_configs_from_file(config_path)
            logger.info(f"Loaded configuration from {config_path}")
        except Exception as e:
            logger.error(f"Error loading configuration: {str(e)}")
    
    # Load configurations from environment variables
    config_manager.load_configs_from_env()
    
    # Initialize backup manager
    backup_manager = BackupManager(portainer_client, config_manager)
    
    logger.info("System initialization complete")
    return portainer_client, config_manager, backup_manager


def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments with improved documentation.
    
    Returns:
        Namespace: Parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description='Service-oriented Docker backup system.',
        epilog='For more information, see the documentation.'
    )
    
    parser.add_argument('--log-level', type=str, 
                      choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                      help='Set the logging level (default: INFO)')
    
    parser.add_argument('--config', type=str,
                      help='Path to custom configuration file (JSON or YAML)')
    
    # Create subparsers for commands
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # Backup command
    backup_parser = subparsers.add_parser('backup', 
                                        help='Run backup for all or specific services',
                                        description='Execute backup operations for Docker services.')
    backup_parser.add_argument('--services', type=str, 
                             help='Comma-separated list of services to back up (e.g., "wordpress,mysql,nginx")')
    
    # Status command
    status_parser = subparsers.add_parser('status', 
                                        help='Show backup status',
                                        description='Display the status of all backups.')
    status_parser.add_argument('--output', type=str, choices=['text', 'json'],
                             default='text', 
                             help='Output format: text (human-readable) or json (machine-readable)')
    
    # Retention command
    retention_parser = subparsers.add_parser('retention', 
                                          help='Apply retention policies',
                                          description='Apply retention policies to remove old backups.')
    
    # Schedule command
    if SCHEDULE_AVAILABLE:
        schedule_parser = subparsers.add_parser('schedule', 
                                              help='Run as a daemon with scheduled backups',
                                              description='Run as a daemon with scheduled backups.')
        schedule_parser.add_argument('--interval', type=str, default='24h',
                                   help='Backup interval (e.g., 6h, 12h, 24h). Default: 24h')
        schedule_parser.add_argument('--retention-interval', type=str, default='24h',
                                   help='Retention policy interval (e.g., 6h, 24h). Default: 24h')
        schedule_parser.add_argument('--no-initial-backup', action='store_true',
                                   help='Skip initial backup when starting the scheduler')
    
    args = parser.parse_args()
    
    # If no command specified, show help
    if not args.command:
        parser.print_help()
        sys.exit(1)
        
    return args


def setup_scheduling(backup_manager: BackupManager, interval: str, 
                    retention_interval: str) -> None:
    """
    Set up backup scheduling.
    
    Args:
        backup_manager (BackupManager): Backup manager instance.
        interval (str): Backup interval (e.g., 6h, 12h, 24h).
        retention_interval (str): Retention policy interval.
    """
    if not SCHEDULE_AVAILABLE:
        logger.error("Schedule library not available. Install with: pip install schedule")
        return
    
    # Parse intervals
    try:
        hours = int(interval.rstrip('h'))
        retention_hours = int(retention_interval.rstrip('h'))
    except ValueError:
        logger.error(f"Invalid interval format: {interval} or {retention_interval}")
        return
    
    # Schedule backups
    logger.info(f"Scheduling backups every {hours} hours")
    schedule.every(hours).hours.do(backup_manager.run_backups)
    
    # Schedule retention policy
    logger.info(f"Scheduling retention policy every {retention_hours} hours")
    schedule.every(retention_hours).hours.do(backup_manager.apply_retention_policy)
    
    # Run retention policy immediately
    backup_manager.apply_retention_policy()
    
    # Run main scheduling loop
    while not shutdown_event.is_set():
        schedule.run_pending()
        time.sleep(60)  # Check every minute


def print_status(status: Dict[str, Any], output_format: str = 'text') -> None:
    """
    Print backup status in the specified format.
    
    Args:
        status (dict): Backup status dictionary.
        output_format (str): Output format ('text' or 'json').
    """
    if output_format == 'json':
        print(json.dumps(status, indent=2))
        return
    
    # Text format
    print("\n=== Docker Backup System Status ===")
    print(f"Timestamp: {status['timestamp']}")
    print(f"Backup Directory: {status['backup_directory']}")
    print(f"Total Storage: {status['storage']['total_size']} MB")
    print(f"Total Backups: {status['storage']['backup_count']}")
    
    if status['active_backups']:
        print(f"\nActive Backups: {', '.join(status['active_backups'])}")
    
    print("\n=== Services ===")
    for service_name, service_info in sorted(status['services'].items()):
        print(f"\n* {service_name}")
        print(f"  Backups: {service_info['backup_count']}")
        print(f"  Size: {service_info['total_size_mb']} MB")
        
        if service_info['latest_backup']:
            latest = service_info['latest_backup']
            print(f"  Latest: {latest['timestamp']} ({latest['size_mb']} MB)")


def main() -> None:
    """
    Main entry point with standardized error handling.
    """
    try:
        # Parse command-line arguments
        args = parse_args()
        
        # Set log level from args if provided
        if args.log_level:
            os.environ['LOG_LEVEL'] = args.log_level
        
        # Set config file from args if provided
        if args.config:
            os.environ['CONFIG_FILE'] = args.config
        
        # Initialize components with proper error handling
        try:
            portainer_client, config_manager, backup_manager = initialize_components()
        except Exception as e:
            print(f"Error initializing components: {str(e)}")
            sys.exit(1)
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, lambda sig, frame: handle_signal(sig, frame))
        signal.signal(signal.SIGTERM, lambda sig, frame: handle_signal(sig, frame))

        # Execute command with standardized error handling
        if args.command == 'backup':
            try:
                # Run backup
                services = args.services.split(',') if args.services else None
                results = backup_manager.run_backups(services)
                
                # Print summary
                success_count = sum(1 for result in results.values() if result)
                print(f"\nBackup completed: {success_count}/{len(results)} successful")
                
                # Exit with error code if any backup failed
                if success_count < len(results):
                    sys.exit(1)
            except Exception as e:
                logger.error(f"Error executing backup command: {str(e)}")
                print(f"Error executing backup command: {str(e)}")
                sys.exit(1)
                
        elif args.command == 'status':
            try:
                # Show backup status
                status = backup_manager.get_backup_status()
                print_status(status, args.output)
            except Exception as e:
                logger.error(f"Error executing status command: {str(e)}")
                print(f"Error executing status command: {str(e)}")
                sys.exit(1)
            
        elif args.command == 'retention':
            try:
                # Apply retention policies
                count = backup_manager.apply_retention_policy()
                print(f"\nRetention policy applied: {count} backups removed")
            except Exception as e:
                logger.error(f"Error executing retention command: {str(e)}")
                print(f"Error executing retention command: {str(e)}")
                sys.exit(1)
            
        elif args.command == 'schedule' and SCHEDULE_AVAILABLE:
            try:
                # Run as a daemon with scheduled backups
                print(f"Starting scheduled backups (interval: {args.interval})")
                print("Press Ctrl+C to exit")
                
                # Run backup immediately on startup
                backup_manager.run_backups()
                
                # Set up scheduling
                scheduler_thread = threading.Thread(
                    target=setup_scheduling,
                    args=(backup_manager, args.interval, args.retention_interval),
                    daemon=True
                )
                scheduler_thread.start()
                
                # Wait for shutdown event
                try:
                    while not shutdown_event.is_set():
                        time.sleep(1)
                except KeyboardInterrupt:
                    print("\nShutting down...")
                finally:
                    cleanup()
            except Exception as e:
                logger.error(f"Error executing schedule command: {str(e)}")
                print(f"Error executing schedule command: {str(e)}")
                sys.exit(1)
                
        else:
            if args.command == 'schedule' and not SCHEDULE_AVAILABLE:
                print("Error: 'schedule' command requires the 'schedule' package.")
                print("Install with: pip install schedule")
                sys.exit(1)
            else:
                # No command or unknown command
                print("Error: Please specify a command.")
                print("Available commands: backup, status, retention", end="")
                if SCHEDULE_AVAILABLE:
                    print(", schedule")
                else:
                    print()
                sys.exit(1)
    
    except Exception as e:
        # Global exception handler
        print(f"An unexpected error occurred: {str(e)}")
        if logger:
            logger.critical(f"Unhandled exception: {str(e)}", exc_info=True)
        sys.exit(1)


def handle_signal(signal_num: int, frame: Any) -> None:
    """
    Handle termination signals.
    
    Args:
        signal_num: Signal number.
        frame: Current stack frame.
    """
    global logger
    if logger:
        logger.info(f"Received signal {signal_num}, shutting down...")
    
    # Set shutdown event
    shutdown_event.set()


def cleanup() -> None:
    """
    Perform cleanup on shutdown.
    """
    global logger
    if logger:
        logger.info("Cleaning up resources...")
    
    # Add any cleanup tasks here
    time.sleep(1)  # Give pending tasks time to complete
    
    if logger:
        logger.info("Cleanup complete")


if __name__ == "__main__":
    main()
