ARG VERSION=1.0.0-alpha

ARG UID=80920
ARG GID=80920

FROM python:3.12-alpine

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
    curl \
    tzdata \
    shadow

# Create non-root user
RUN addgroup -g $GID appuser && \
    adduser -D -u $UID -G appuser -s /bin/bash appuser

# Create backup directory with proper permissions
RUN mkdir -p /backups && \
    chown -R appuser:appuser /backups

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY utils/docker_utils.py \ 
     utils/archive_utils.py \
     utils/credential_utils.py \
     /app/utils/

COPY main.py \
     logger.py \
     file_backup.py \
     service_backup.py \
     backup_manager.py \
     config_manager.py \
     database_backup.py \
     portainer_client.py \
     retention_manager.py \
     /app/


# Set the correct permissions
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Run the application
CMD ["python", "main.py", "schedule"]

