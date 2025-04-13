# Container-Backup

A service-oriented Docker backup system that automatically discovers and backs up Docker containers with consistent database and file snapshots.

## Overview

Container-Backup is a tool for backing up Docker containers and their associated data. It uses a service-oriented approach that:

1. **Discovers services automatically** by analyzing Docker containers and their relationships
2. **Ensures backup consistency** by capturing database and application files together
3. **Uses hot backups** where possible to minimize service disruption
4. **Falls back to stop/backup/start** when hot backups aren't feasible
5. **Stores backups in a cohesive manner** for straightforward restoration

## Features

- **Service-oriented backups**: Treats applications as services with potentially multiple components
- **Automatic discovery**: Identifies services, databases, and data paths automatically
- **Multi-protocol support**: Works with bind mounts, volumes, and other storage mechanisms
- **Database-aware**: Special handling for PostgreSQL, MySQL, MariaDB, MongoDB, Redis, and SQLite
- **Configurable retention**: Flexible retention policies for managing backup storage
- **Custom configuration**: Override defaults with configuration files or environment variables
- **Low-disruption design**: Minimizes service downtime during backups
- **Portainer integration**: Works with Portainer for stack environment recognition
- **Security-focused**: Optional Docker socket proxy support for enhanced security

## Quick Start

### Using Docker Compose

Create a `docker-compose.yml` file:

```yaml
version: '3.8'

services:
  # Socket proxy for enhanced security (optional)
  docker-socket-proxy:
    image: tecnativa/docker-socket-proxy:latest
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    environment:
      CONTAINERS: 1  # Allow container listing/inspection
      IMAGES: 1      # Allow image operations needed for backups
      NETWORKS: 1    # Allow network information access
      SERVICES: 0    # No need for Swarm services
      VOLUMES: 1     # Allow volume information
      EXEC: 1        # Allow exec commands for database backups
      TASKS: 0       # Disable Swarm task access
      VERSION: 1     # Allow version checking
      STOP: 1
      START: 1
      POST: 1
    restart: unless-stopped

  container-backup:
    image: container-backup:1.1
    container_name: container-backup
    hostname: container-backup
    volumes:
      # Mount with Docker socket proxy
      # - /var/run/docker.sock:/var/run/docker.sock:ro  # Use this without proxy
      - /mnt/backups:/backups:rw                      # Persistent backup storage
      - /mnt/docker:/mnt/docker:ro                    # Read-only access to docker volumes
      - /app/config:/app/config                       # Configuration files
      - /etc/group:/host/etc/group:ro                 # Host's group file for mirroring
      - /etc/passwd:/host/etc/passwd:ro               # Host's passwd file for user discovery
      - ./entrypoint.sh:/entrypoint.sh:ro             # Custom entrypoint script
    environment:
      - TZ=UTC
      - LOG_LEVEL=INFO
      - BACKUP_DIR=/backups
      - MAX_CONCURRENT_BACKUPS=3
      - BACKUP_RETENTION_DAYS=7
      - PORTAINER_URL=https://portainer.example.com
      - PORTAINER_API_KEY=your-api-key-here
      - PORTAINER_INSECURE=false
      - CONFIG_FILE=/app/config/service_configs.json
      - EXCLUDE_FROM_BACKUP=seafile mealie immich
      - PUID=34                                     # User ID for the backup user
      - PGID=34                                     # Group ID for the backup user
      - DOCKER_HOST=tcp://docker-socket-proxy:2375  # Connect to proxy - Remove with direct socket
      - BACKUP_SERVICE_NAMES=container-backup,backup
      - BACKUP_METHOD=mounts
      - EXCLUDE_MOUNT_PATHS=/mnt/media, /mnt/backups, /cache, /var/lib/docker
      - MIRROR_HOST_GROUPS=true
      - NFS_MODE=true                               # Enable NFS-specific handling
      - INSTALL_PACKAGES=shadow                     # Additional packages to install
    restart: unless-stopped
    depends_on:
      - docker-socket-proxy                         # Remove with direct socket
    entrypoint: ["/entrypoint.sh"]
    healthcheck:
      test: ["CMD", "python", "-c", "import os; exit(0 if os.path.exists('/backups') else 1)"]
      interval: 1m
      timeout: 10s
      retries: 3
      start_period: 10s
```

### Running Commands

**Start the backup service:**
```bash
docker-compose up -d
```

**Execute a manual backup:**
```bash
docker exec container-backup python main.py backup
```

**View backup status:**
```bash
docker exec container-backup python main.py status
```

**Apply retention policies:**
```bash
docker exec container-backup python main.py retention
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `BACKUP_DIR` | Directory to store backups | `/backups` |
| `BACKUP_METHOD` | Backup method (`mounts` or `container_cp`) | `mounts` |
| `BACKUP_RETENTION_DAYS` | Number of days to keep backups | `7` |
| `BACKUP_SERVICE_NAMES` | Names of this backup service for self-exclusion | `container-backup,backup` |
| `CONFIG_FILE` | Path to configuration file | `/app/config/service_configs.json` |
| `DOCKER_HOST` | Docker socket or proxy URL | *empty* |
| `DOCKER_READ_ONLY` | Restrict Docker API to read-only operations | `true` |
| `EXCLUDE_FROM_BACKUP` | Space-separated list of services to exclude | *empty* |
| `EXCLUDE_MOUNT_PATHS` | Comma-separated list of paths to exclude | *empty* |
| `INSTALL_PACKAGES` | Additional Alpine packages to install | *empty* |
| `LOG_LEVEL` | Logging level (INFO, DEBUG, etc.) | `INFO` |
| `MAX_CONCURRENT_BACKUPS` | Maximum number of concurrent backups | `3` |
| `MIRROR_HOST_GROUPS` | Mirror host user groups | `true` |
| `NFS_MODE` | Enable NFS-specific optimizations | `false` |
| `PGID` | Group ID to run as | *current* |
| `PORTAINER_API_KEY` | Portainer API key | *required* |
| `PORTAINER_INSECURE` | Allow insecure connections to Portainer | `false` |
| `PORTAINER_URL` | Portainer API URL | *required* |
| `PUID` | User ID to run as | *current* |
| `TZ` | Timezone | `UTC` |

### Service Configuration

Create a `service_configs.json` file in your config directory:

```json
{
  "wordpress": {
    "database": {
      "type": "mysql",
      "requires_stopping": false,
      "container_patterns": ["*mysql*", "*mariadb*"]
    },
    "files": {
      "data_paths": ["wp-content"],
      "requires_stopping": false,
      "exclusions": ["wp-content/cache/*", "wp-content/debug.log"]
    },
    "global": {
      "backup_retention": 7,
      "exclude_from_backup": false,
      "priority": 10
    }
  },
  "nextcloud": {
    "database": {
      "type": "postgres",
      "requires_stopping": false,
      "container_patterns": ["*postgres*", "*db*"]
    },
    "files": {
      "data_paths": ["data", "config", "themes", "apps"],
      "requires_stopping": true,
      "exclusions": ["data/appdata*/cache/*", "data/*/cache/*", "data/*/files_trashbin/*"]
    },
    "global": {
      "mixed_retention": {
        "daily": 7,
        "weekly": 4,
        "monthly": 2
      },
      "priority": 20
    }
  }
}
```

## System Requirements

- Docker 20.10.0 or later
- Python 3.9 or later
- Access to the Docker socket or Docker socket proxy
- Volume mounts for persistent storage

## Advanced Configuration

### Custom Entrypoint Script

You can customize the entrypoint script to handle special use cases like complex group memberships, particularly in NFS environments.

```bash
#!/bin/bash

# Function to handle errors
error() { 
    echo "ERROR: $1"
    exit 1
}

# Function to log information
log() { 
    echo "INFO: $1"
}

# ... Rest of the entrypoint script ...
```

### NFS Environments

When using NFS mounts, enable `NFS_MODE=true` and ensure your host's groups are properly mirrored with `MIRROR_HOST_GROUPS=true`.

## Troubleshooting

### Common Issues

#### Permission Denied Errors

If you see permission denied errors when accessing bind mounts:

1. Check that the PUID/PGID match the owner of your backup directories
2. Ensure `MIRROR_HOST_GROUPS=true` and mount `/etc/group:/host/etc/group:ro`
3. For NFS mounts, enable `NFS_MODE=true`

#### Docker Socket Access

If you see Docker API access errors:

1. Verify the container has access to the Docker socket
2. Check that the socket has proper permissions
3. Consider using the Docker socket proxy for improved security

#### Database Backup Failures

If database backups fail:

1. Ensure the database container is accessible
2. Check that the backup user has sufficient permissions
3. Verify that database paths are correctly detected

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request
