FROM python:3.12-alpine

ARG VERSION=1.0.26-alpha
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
    docker-cli \
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

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Set the correct permissions
RUN chown -R appuser:appuser /app

# Switch to the entrypoint script
ENTRYPOINT ["/entrypoint.sh"]

# Run the application
CMD ["python", "main.py", "schedule"]

