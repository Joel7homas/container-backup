# Container-Backup 1.1.0 Release Notes

## What's New

### Major Features

- **Enhanced Permission System**: Redesigned permission handling framework with group mirroring.
- **NFS Support**: Added special handling for NFS-based mounts with permission optimizations.
- **External Entrypoint Script**: New customizable entrypoint script for advanced group management.
- **Mount-Based Backups**: Added support for dedicated mount-based backup method.
- **Docker Socket Proxy Support**: Enhanced security with Docker socket proxy compatibility.

### Usability Improvements

- **Improved Error Handling**: Better error handling and reporting for all operations.
- **Enhanced Path Exclusions**: More intelligent path filtering to reduce unnecessary errors.
- **Group Integration**: Automatic mirroring of host groups for seamless permission handling.
- **Better Shell Compatibility**: Fixed shell command compatibility issues in containers.
- **Streamlined Configuration**: Simplified configuration with sane defaults.

### New Environment Variables

- `BACKUP_METHOD`: Choose between `mounts` and `container_cp` backup methods.
- `BACKUP_SERVICE_NAMES`: Specify service names to prevent self-backup.
- `EXCLUDE_MOUNT_PATHS`: Paths to exclude from backup attempts.
- `MIRROR_HOST_GROUPS`: Enable mirroring of host user groups.
- `NFS_MODE`: Enable NFS-specific optimizations.
- `INSTALL_PACKAGES`: Additional packages to install at runtime.

## Bug Fixes

- Fixed self-backup termination issue that could stop the backup container
- Resolved BusyBox shell compatibility issues with test commands
- Fixed group membership problems for the backup user
- Corrected exclusion filter parsing for space-separated service names
- Improved lock file mechanism to use proper PIDs and timeout detection
- Added better handling of inaccessible directories
- Fixed Redis client path detection in containers

## Architectural Improvements

- More modular code organization with improved separation of concerns
- Better error handling and logging throughout the codebase
- Enhanced security with read-only Docker API access
- More robust container state handling with improved stop/start logic
- Better isolation of components through clear interfaces

## Documentation

- Comprehensive README with configuration options and examples
- Environment variable reference with descriptions
- Docker Compose examples for various scenarios
- Troubleshooting guide for common issues
- Updated Issues Register and Enhancement Ideas documents

## System Requirements

- Docker 20.10.0 or later
- Python 3.9 or later
- Access to Docker socket or Docker socket proxy
- Volume mounts for persistent storage

## Installation

### Using Docker Compose

Create a `docker-compose.yml` file with the necessary configuration (see README for details), then run:

```bash
docker-compose up -d
```

### Manual Commands

Execute backups manually:

```bash
docker exec container-backup python main.py backup
```

Get backup status:

```bash
docker exec container-backup python main.py status
```

## Upgrading from Alpha Versions

1. Backup your existing configuration
2. Update your Docker Compose file with the new environment variables
3. Add the entrypoint script to your project directory
4. Update your image reference to `container-backup:1.1.0`
5. Restart the service: `docker-compose down && docker-compose up -d`

## Known Issues

- Some NFS mounted directories with restrictive permissions may still be inaccessible
- Very large files may experience performance issues during backup
- Some database types may require specific configurations for optimal backup

## Coming Soon

We're already working on exciting new features for upcoming releases:

- Backup verification system
- ZFS snapshot integration
- Remote storage support
- Notification system
- Restore functionality

## Thank You

A huge thank you to all the users who provided feedback, reported issues, and helped test the alpha versions. Your contributions have been instrumental in reaching this stable release.

## License

This project is released under the zlib License.

