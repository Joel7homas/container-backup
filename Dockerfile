FROM python:3.12-alpine

ARG VERSION=1.0.5-alpha
ARG UID=80920
ARG GID=80920

# Set work directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    VERSION=${VERSION}

LABEL version=${VERSION}

# Install system dependencies
RUN apk update && \
    apk add --no-cache \
    bash \
    su-exec \
    curl \
    tzdata \
    shadow

# Create non-root user
RUN addgroup -g ${GID} appuser && \
    adduser -D -u ${UID} -G appuser -s /bin/bash appuser

# Create backup directory with proper permissions
RUN mkdir -p /backups && \
    chown -R appuser:appuser /backups

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY utils/ /app/utils/

COPY main.py \
     logger.py \
     file_backup.py \
     service_backup.py \
     backup_manager.py \
     config_manager.py \
     database_backup.py \
     portainer_client.py \
     retention_manager.py \
     service_discovery.py \
     /app/

# Set the correct permissions
RUN chown -R appuser:appuser /app

# Create an improved entrypoint script that handles Docker group and runtime UID/GID
RUN echo '#!/bin/bash' > /entrypoint.sh && \
    echo '' >> /entrypoint.sh && \
    echo '# Function to handle errors' >> /entrypoint.sh && \
    echo 'error() { echo "ERROR: $1"; exit 1; }' >> /entrypoint.sh && \
    echo '' >> /entrypoint.sh && \
    echo '# Function to log information' >> /entrypoint.sh && \
    echo 'log() { echo "INFO: $1"; }' >> /entrypoint.sh && \
    echo '' >> /entrypoint.sh && \
    echo '# Check if Docker socket exists and get its group ID' >> /entrypoint.sh && \
    echo 'if [ -e /var/run/docker.sock ]; then' >> /entrypoint.sh && \
    echo '  # Get the GID of the docker socket' >> /entrypoint.sh && \
    echo '  DOCKER_SOCKET_GID=$(stat -c "%g" /var/run/docker.sock)' >> /entrypoint.sh && \
    echo '  log "Docker socket found with GID: ${DOCKER_SOCKET_GID}"' >> /entrypoint.sh && \
    echo '' >> /entrypoint.sh && \
    echo '  # Use DOCKER_GID from environment if provided, otherwise use detected GID' >> /entrypoint.sh && \
    echo '  DOCKER_GROUP_GID=${DOCKER_GID:-$DOCKER_SOCKET_GID}' >> /entrypoint.sh && \
    echo '  log "Using Docker GID: ${DOCKER_GROUP_GID}"' >> /entrypoint.sh && \
    echo '' >> /entrypoint.sh && \
    echo '  # Create the docker group if it does not exist yet' >> /entrypoint.sh && \
    echo '  if ! getent group ${DOCKER_GROUP_GID} > /dev/null; then' >> /entrypoint.sh && \
    echo '    log "Creating docker group with GID: ${DOCKER_GROUP_GID}"' >> /entrypoint.sh && \
    echo '    addgroup -g ${DOCKER_GROUP_GID} docker || error "Failed to create docker group"' >> /entrypoint.sh && \
    echo '  else' >> /entrypoint.sh && \
    echo '    # Get the name of the group with this GID' >> /entrypoint.sh && \
    echo '    EXISTING_GROUP=$(getent group ${DOCKER_GROUP_GID} | cut -d: -f1)' >> /entrypoint.sh && \
    echo '    if [ "$EXISTING_GROUP" != "docker" ]; then' >> /entrypoint.sh && \
    echo '      log "Renaming existing group ${EXISTING_GROUP} to docker"' >> /entrypoint.sh && \
    echo '      sed -i "s/^${EXISTING_GROUP}:/docker:/" /etc/group || error "Failed to rename group"' >> /entrypoint.sh && \
    echo '    else' >> /entrypoint.sh && \
    echo '      log "Docker group already exists with correct GID"' >> /entrypoint.sh && \
    echo '    fi' >> /entrypoint.sh && \
    echo '  fi' >> /entrypoint.sh && \
    echo 'else' >> /entrypoint.sh && \
    echo '  log "Docker socket not found at /var/run/docker.sock"' >> /entrypoint.sh && \
    echo 'fi' >> /entrypoint.sh && \
    echo '' >> /entrypoint.sh && \
    echo '# Handle runtime UID/GID changes' >> /entrypoint.sh && \
    echo 'if [ ! -z "${PUID}" ] && [ ! -z "${PGID}" ]; then' >> /entrypoint.sh && \
    echo '  log "Changing user/group IDs at runtime to ${PUID}:${PGID}"' >> /entrypoint.sh && \
    echo '  # Try to delete user and group, but don'\''t fail if not found' >> /entrypoint.sh && \
    echo '  deluser appuser 2>/dev/null || true' >> /entrypoint.sh && \
    echo '  delgroup appuser 2>/dev/null || true' >> /entrypoint.sh && \
    echo '' >> /entrypoint.sh && \
    echo '  # Create group and user with new IDs' >> /entrypoint.sh && \
    echo '  addgroup -g ${PGID} appuser || error "Failed to create appuser group with GID ${PGID}"' >> /entrypoint.sh && \
    echo '  adduser -D -u ${PUID} -G appuser -s /bin/sh appuser || error "Failed to create appuser with UID ${PUID}"' >> /entrypoint.sh && \
    echo '' >> /entrypoint.sh && \
    echo '  # Set correct permissions on directories' >> /entrypoint.sh && \
    echo '  chown -R appuser:appuser /app /backups' >> /entrypoint.sh && \
    echo 'fi' >> /entrypoint.sh && \
    echo '' >> /entrypoint.sh && \
    echo '# Add appuser to the docker group if it exists' >> /entrypoint.sh && \
    echo 'if getent group docker > /dev/null; then' >> /entrypoint.sh && \
    echo '  log "Adding appuser to docker group"' >> /entrypoint.sh && \
    echo '  adduser appuser docker || error "Failed to add appuser to docker group"' >> /entrypoint.sh && \
    echo 'fi' >> /entrypoint.sh && \
    echo '' >> /entrypoint.sh && \
    echo '# Print the final user/group setup' >> /entrypoint.sh && \
    echo 'log "Running as:"' >> /entrypoint.sh && \
    echo 'su-exec appuser id' >> /entrypoint.sh && \
    echo '' >> /entrypoint.sh && \
    echo '# Execute the command as appuser' >> /entrypoint.sh && \
    echo 'log "Starting application with command: $@"' >> /entrypoint.sh && \
    echo 'exec su-exec appuser "$@"' >> /entrypoint.sh && \
    chmod +x /entrypoint.sh

# Switch to the entrypoint script
ENTRYPOINT ["/entrypoint.sh"]

# Run the application
CMD ["python", "main.py", "schedule"]

