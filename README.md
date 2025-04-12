# Container-backup

A tool for discovering and creating backups for containerized services in Docker environments that use Portainer.

## Overview

Container-backup was conceived as a safety net for Docker projects in a lab environment where services and applications are frequently built and torn down without much attention toward production readiness considerations, or in a homelab environment where administrators need a stop-gap solution until they can get around to setting up a proper backup & recovery solution for each application (at which point, exclusions can be added). 

The tool uses Docker to discover running applications and treats each application as a service with multiple components, so that all parts of a service (databases, file systems, etc.) are backed up together in a consistent manner to avoid database and file backups being out of sync. 

### Key Features

- **Service-Oriented Architecture**: Treats each application as a complete service with potentially multiple components
- **Automatic Service Discovery**: Identifies Docker services and their components using Portainer integration
- **Comprehensive Backup Strategy**: Ensures database and file backups are consistent
- **Flexible Configuration**: Configure backup behavior per service, with sensible defaults
- **Intelligent Retention Policies**: Supports time-based, count-based, and mixed retention strategies
- **Hot Backup Support**: Minimizes service disruption by using hot backups where possible
- **Graceful Fallback**: Falls back to stop/backup/start when hot backups aren't feasible
- **Multiple Database Support**: Works with PostgreSQL, MySQL/MariaDB, SQLite, MongoDB, and Redis
- **Parallel Processing**: Runs backups concurrently with configurable limits
- **Robust Error Handling**: Ensures backup operations are reliable and recoverable

## Installation

### Prerequisites

- Docker and Docker Compose
- Portainer for stack and credential management
- Access to the Docker socket on the host system

### Setup with Docker Compose

1. Clone the repository:
   ```bash
   git clone https://github.com/Joel7homas/container-backup.git
   cd container-backup
   ```

2. Create necessary directories:
   ```bash
   mkdir -p backups config
   chmod 777 backups  # Ensure the container can write to this directory
   ```

3. Copy the sample configuration files:
   ```bash
   cp stack.env.example stack.env
   cp config/service_configs.json.example config/service_configs.json
   ```

4. Edit the environment variables in `stack.env` to match your environment:
   ```bash
   PORTAINER_URL=https://your-portainer-url/
   PORTAINER_API_KEY=your-api-key-here
   ```

5. Deploy with Docker Compose:
   ```bash
   docker-compose --env-file stack.env up -d
   ```

### Portainer API Key

To generate a Portainer API key:

1. Log in to your Portainer instance
2. Go to your user profile (click on your username in the top-right corner)
3. Select "Access Tokens"
4. Click "Add access token"
5. Enter a description and set an expiration date
6. Copy the token value and use it as `PORTAINER_API_KEY` in your stack.env file

## Usage

The system can be used in multiple ways:

### Scheduled Backups (Default)

By default, the system runs as a daemon with scheduled backups:

```bash
docker exec -it container-backup python main.py schedule --interval 24h
```

This will run backups every 24 hours and apply retention policies accordingly.

### Manual Backup

To manually trigger backups:

```bash
# Back up all services
docker exec -it container-backup python main.py backup

# Back up specific services
docker exec -it container-backup python main.py backup --services wordpress,mysql
```

### Apply Retention Policies

To manually apply retention policies:

```bash
docker exec -it container-backup python main.py retention
```

### Check Backup Status

To view the current backup status:

```bash
# Text format (default)
docker exec -it container-backup python main.py status

# JSON format
docker exec -it container-backup python main.py status --output json
```

## Configuration

The system can be configured in multiple ways:

### Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `PORTAINER_URL` | URL to your Portainer instance | none | Yes |
| `PORTAINER_API_KEY` | API key for Portainer access | none | Yes |
| `PORTAINER_INSECURE` | Disable SSL verification | false | No |
| `LOG_LEVEL` | Log level (DEBUG, INFO, WARNING, ERROR) | INFO | No |
| `BACKUP_DIR` | Directory to store backups | /backups | No |
| `MAX_CONCURRENT_BACKUPS` | Maximum number of parallel backups | 3 | No |
| `BACKUP_RETENTION_DAYS` | Default number of days to keep backups | 7 | No |
| `EXCLUDE_FROM_BACKUP` | Space-separated list of services to exclude | empty | No |
| `CONFIG_FILE` | Path to service configuration file | none | No |
| `TZ` | Timezone for scheduling and logging | UTC | No |

### Service Configuration

Service configurations can be defined in a JSON file and mounted to `/app/config/service_configs.json`:

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
  }
}
```

#### Configuration Options

- **database**:
  - `type`: Database type (postgres, mysql, sqlite, mongodb, redis)
  - `requires_stopping`: Whether to stop containers for backup
  - `container_patterns`: Patterns to identify database containers
  - `credentials`: Optional hardcoded credentials

- **files**:
  - `data_paths`: Paths to back up
  - `requires_stopping`: Whether to stop containers for backup
  - `exclusions`: Patterns to exclude from backup

- **global**:
  - `backup_retention`: Days to keep backups
  - `exclude_from_backup`: Whether to exclude this service
  - `priority`: Backup priority (lower numbers first)
  - `mixed_retention`: Configure complex retention policies

### Environment-Based Configuration

Service configurations can also be provided via environment variables:

```bash
SERVICE_CONFIG_WORDPRESS={"database":{"type":"mysql"},"files":{"exclusions":["cache"]}}
```

## How It Works

### Service Discovery

The system discovers services based on:

1. Docker containers running on the host
2. Container labels and relationships
3. Portainer stack information

Services are identified based on Portainer stacks, Docker Compose projects, or container relationships.

### Backup Process

For each service:

1. **Preparation**:
   - Determine if service requires stopping
   - Create temporary staging directory

2. **Database Backup**:
   - For each database container:
     - Retrieve credentials from Portainer
     - Execute appropriate backup command based on database type
     - Store compressed backup in staging directory

3. **File Backup**:
   - For each data path:
     - Apply exclusion filters
     - Archive and compress files to staging directory

4. **Consolidation**:
   - Create single timestamp-named archive containing all service components
   - Move to final backup location

5. **Cleanup**:
   - Remove temporary files
   - Start any stopped containers
   - Apply retention policies

### Retention Policies

The system supports multiple retention strategies:

- **Time-Based**: Keep backups for X days
- **Count-Based**: Keep the most recent X backups
- **Mixed**: Keep daily backups for a week, weekly for a month, monthly for a year

### Database Support

- **PostgreSQL**: Uses `pg_dump` with properly sourced credentials
- **MySQL/MariaDB**: Uses `mysqldump` with properly sourced credentials
- **SQLite**: Uses file-based backup of `.db`/`.sqlite`/`.sqlite3` files
- **MongoDB**: Uses `mongodump` with properly sourced credentials
- **Redis**: Uses `redis-cli --rdb` command or RDB file copy

## Backup File Structure

Backups are stored in the following format:

```
/backups/
├── service1_20250409_010000.tar.gz
├── service1_20250410_010000.tar.gz
└── service2_20250409_010000.tar.gz
```

Each archive contains:

```
service_name_timestamp/
├── databases/
│   └── db_container_name.sql.gz
├── files/
│   └── app_data.tar.gz
└── metadata.json
```

## Troubleshooting

### Common Issues

**No backups are being created**:
- Check container logs: `docker logs container-backup`
- Verify Portainer URL and API key are correct
- Ensure the backup directory has appropriate permissions
- Check if Docker socket is properly mounted

**Error connecting to Portainer**:
- Verify the Portainer URL is accessible from the container
- Ensure the API key has not expired
- Check network connectivity
- Try setting `PORTAINER_INSECURE=true` if using self-signed certificates

**Cannot find database credentials**:
- Verify that environment variables in your stack contain database credentials
- Check container logs for available environment variables
- Ensure the container is associated with a Portainer stack

**Database backup fails**:
- Check if the database type is correctly identified
- Verify that the necessary backup tools are available in the container
- Check if the database is accessible (network, credentials)

### Viewing Logs

```bash
# View all logs
docker logs container-backup

# View only error logs
docker logs container-backup 2>&1 | grep ERROR

# Follow logs in real-time
docker logs -f container-backup
```

## Advanced Usage

### Custom Retention Policies

The system supports sophisticated retention policies:

```json
"global": {
  "mixed_retention": {
    "daily": 7,   // Keep 7 daily backups
    "weekly": 4,  // Keep 4 weekly backups
    "monthly": 3  // Keep 3 monthly backups
  }
}
```

### Multiple Database Handling

For services with multiple databases:

```json
"myservice": {
  "database": {
    "type": "postgres",
    "container_patterns": ["*postgres*", "*pg*", "*postgresql*"]
  }
}
```

### Excluding Paths from Backup

To exclude certain paths from file backups:

```json
"files": {
  "exclusions": [
    "tmp/*",
    "cache/*",
    "*.log",
    "node_modules/*"
  ]
}
```

### Backing Up Without Stopping Services

For most databases, hot backups are supported:

```json
"database": {
  "requires_stopping": false
}
```

## Security Considerations

- The container needs read access to the Docker socket, which gives it significant privileges
- Database credentials are retrieved and used in memory but not stored persistently
- Consider restricting the Portainer API key permissions to only what's necessary
- Encrypt backup storage or transfer if containing sensitive data

## Contributing

Contributions are welcome:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

Please ensure your code follows the project's style guidelines and includes appropriate tests.

## License

This project is distributed under the zlib License. See the LICENSE file for details.

## Disclaimer

This tool is designed as a comprehensive backup solution for Docker-based services. While it aims to be robust and reliable, always ensure you have a proper backup and recovery strategy in place for mission-critical systems.

