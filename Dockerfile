FROM python:3.12-alpine

ARG VERSION=1.0.1-alpha
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
RUN addgroup -g 80920 appuser && \
    adduser -D -u 80920 -G appuser -s /bin/bash appuser

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

# Create an entrypoint script to allow runtime UID/GID modification
RUN echo '#!/bin/bash' > /entrypoint.sh && \
    echo 'if [ ! -z "$PUID" ] && [ ! -z "$PGID" ]; then' >> /entrypoint.sh && \
    echo '  echo "Changing user/group IDs at runtime to $PUID:$PGID"' >> /entrypoint.sh && \
    echo '  groupmod -g $PGID appuser' >> /entrypoint.sh && \
    echo '  usermod -u $PUID appuser' >> /entrypoint.sh && \
    echo '  chown -R appuser:appuser /app /backups' >> /entrypoint.sh && \
    echo 'fi' >> /entrypoint.sh && \
    echo 'exec su-exec appuser "$@"' >> /entrypoint.sh && \
    chmod +x /entrypoint.sh

# Switch to the entrypoint script
ENTRYPOINT ["/entrypoint.sh"]

# Run the application
CMD ["python", "main.py", "schedule"]

