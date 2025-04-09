# Docker Database Backup Tool

A robust solution for automated database backups in Docker environments with Portainer integration.

## Overview

This tool automatically discovers and backs up databases running in Docker containers. It integrates with Portainer to retrieve database credentials from stack environment variables, eliminating the need for manual credential management.

### Key Features

- **Automatic Database Discovery**: Identifies database containers (PostgreSQL, MySQL/MariaDB, SQLite) in your Docker environment
- **Credential Management**: Retrieves database credentials from Portainer stack configurations
- **Scheduled Backups**: Configurable scheduled backup execution
- **Multiple Database Support**: Works with PostgreSQL, MySQL/MariaDB, and SQLite databases
- **Compression**: Automatically compresses backups to save storage space
- **Selective Exclusion**: Ability to exclude specific stacks from the backup process

## Requirements

- **Docker**: Running Docker environment
- **Portainer**: For stack and credential management
- **Python 3.x**: Container is based on Python Alpine
- **Storage Volume**: Mounted volume for backup storage

## Installation

### Using Docker Compose

1. Create a `docker-compose.yml` file:

```yaml
version: '3.8'
services:
  database-backup:
    image: python:3.12-alpine
    container_name: database-backup
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /path/to/backups:/backups:rw
      - /path/to/backup-script.py:/app/backup-script.py:ro
    environment:
      - TZ=America/Denver
      - BACKUP_RETENTION_DAYS=7
      - PORTAINER_URL=${PORTAINER_URL}
      - PORTAINER_API_KEY=${PORTAINER_TOKEN}
      - EXCLUDE_FROM_BACKUP=${EXCLUDE_FROM_BACKUP}
      - PYTHONUNBUFFERED=1
      - CRON_SCHEDULE=01:00  # Run at 1 AM
    working_dir: /app
    command: sh -c "pip install docker requests schedule && python3 backup-script.py"
    restart: unless-stopped
```

2. Create a `stack.env` file with your configuration:

```
PORTAINER_URL=https://your_portainer_url/
PORTAINER_TOKEN=YOUR_API_TOKEN
EXCLUDE_FROM_BACKUP=stack1 stack2 stack3
```

3. Deploy the stack:
```bash
docker-compose up -d
```

## Configuration

### Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `PORTAINER_URL` | URL to your Portainer instance | none | Yes |
| `PORTAINER_API_KEY` | API key for Portainer access | none | Yes |
| `EXCLUDE_FROM_BACKUP` | Space-separated list of stack names to exclude | empty | No |
| `CRON_SCHEDULE` | Time to run backup in 24h format (HH:MM) | none | No |
| `BACKUP_RETENTION_DAYS` | Number of days to keep backups | 7 | No |
| `TZ` | Timezone for scheduling and logging | UTC | No |
| `PYTHONUNBUFFERED` | Enable unbuffered Python output | 1 | No |

### Portainer API Key

To generate a Portainer API key:

1. Log in to your Portainer instance
2. Go to your user profile (click on your username in the top-right corner)
3. Select "Access Tokens"
4. Click "Add access token"
5. Enter a description and set an expiration date
6. Copy the token value and use it as `PORTAINER_TOKEN`

## How It Works

### Database Discovery

The tool scans running Docker containers and identifies database containers by their image names:
- PostgreSQL: Images containing "postgres" or "pgvecto"
- MySQL/MariaDB: Images containing "mysql" or "mariadb"
- SQLite: Images containing "sqlite"

### Credential Retrieval

For each database container:

1. Determines the associated stack name from container labels
2. Retrieves environment variables from Portainer for that stack
3. Extracts database credentials using common naming patterns

Credential patterns for different database types:

**PostgreSQL:**
- Username: `DB_USER`, `POSTGRES_USER`, `PGUSER`, etc.
- Password: `DB_PASSWORD`, `POSTGRES_PASSWORD`, `PGPASSWORD`, etc.
- Database: `DB_NAME`, `POSTGRES_DB`, `DATABASE_NAME`, etc.

**MySQL/MariaDB:**
- Uses root credentials from `MYSQL_ROOT_PASSWORD`
- Database from `DB_NAME`, `MYSQL_DATABASE`, etc.

**SQLite:**
- Automatically finds `.sqlite`, `.db`, and `.sqlite3` files in the container

### Backup Process

For each identified database:

1. **PostgreSQL/MySQL**: Executes a database dump command inside the container
2. **SQLite**: Creates a copy of the database file
3. Compresses the backup using gzip
4. Saves the backup to the mounted volume with a timestamp

### Scheduling

When `CRON_SCHEDULE` is set, the tool runs at the specified time each day. Otherwise, it executes immediately and exits.

## Backup Files

Backups are saved in the mounted volume with the naming format:
```
<container_name>_<timestamp>.sql.gz
```

For example: `postgres_20250409_010000.sql.gz`

## Limiting Scope

To exclude certain stacks from backup:

1. Set the `EXCLUDE_FROM_BACKUP` environment variable with space-separated stack names
2. These stacks' containers will be identified but skipped during backup

## Troubleshooting

### Common Issues

**No backups are being created:**
- Check container logs: `docker logs database-backup`
- Verify Portainer URL and API key are correct
- Ensure the backup directory has appropriate permissions
- Check if Docker socket is properly mounted

**Error connecting to Portainer:**
- Verify the Portainer URL is accessible from the container
- Ensure the API key has not expired
- Check network connectivity

**Cannot find database credentials:**
- Verify that environment variables in your stack contain database credentials
- Check container logs for available environment variables
- Ensure the container is associated with a Portainer stack

### Logs

To view detailed logs:
```bash
docker logs database-backup
```

The logs show:
- Container discovery process
- Environment variables found for each stack
- Credential extraction attempts
- Backup operations and their success/failure

## Maintenance

### Backup Rotation

The script doesn't currently implement automatic backup rotation. To manage backup storage:

1. Set up a separate process to remove old backups:
```bash
find /path/to/backups -name "*.sql.gz" -mtime +7 -delete
```

2. Or use a specialized backup rotation tool like `logrotate`

### Updating

To update the script:

1. Modify the `backup-script.py` file
2. Restart the container:
```bash
docker restart database-backup
```

## Security Considerations

- The container needs read access to the Docker socket, which gives it significant privileges
- Database credentials are retrieved and used in memory but not stored persistently
- Consider restricting the Portainer API key permissions to only what's necessary
- Encrypt backup storage or transfer if containing sensitive data

## Advanced Usage

### Manual Backup Trigger

To manually trigger a backup:

```bash
docker restart database-backup
```

Or for a more elegant solution:

```bash
docker exec database-backup python3 backup-script.py
```

### Custom Credential Handling

If your database uses non-standard environment variable names, you might need to modify the script to recognize these patterns.

Look for the `get_container_credentials` method in the script, which contains pattern matching logic for credentials.

## Contributing

This is an open-source project. Contributions are welcome:

- Bug reports
- Feature enhancements
- Documentation improvements
- Code optimizations

## License

This project is distributed under the zlib License.

## Disclaimer

This tool is designed as a "better-than-nothing" backup solution for lab environments. For production systems, consider using a more robust, dedicated backup solution with proper monitoring, validation, and recovery capabilities.

