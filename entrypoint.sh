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

# Check if Docker socket exists and get its group ID
if test -e /var/run/docker.sock
then
  # Get the GID of the docker socket
  DOCKER_SOCKET_GID=$(stat -c "%g" /var/run/docker.sock)
  log "Docker socket found with GID: ${DOCKER_SOCKET_GID}"

  # Use DOCKER_GID from environment if provided, otherwise use detected GID
  DOCKER_GROUP_GID=${DOCKER_GID:-$DOCKER_SOCKET_GID}
  log "Using Docker GID: ${DOCKER_GROUP_GID}"

  # Create the docker group if it does not exist yet
  if ! getent group ${DOCKER_GROUP_GID} > /dev/null
  then
    log "Creating docker group with GID: ${DOCKER_GROUP_GID}"
    addgroup -g ${DOCKER_GROUP_GID} docker || error "Failed to create docker group"
  else
    # Get the name of the group with this GID
    EXISTING_GROUP=$(getent group ${DOCKER_GROUP_GID} | cut -d: -f1)
    if test "$EXISTING_GROUP" != "docker"
    then
      log "Renaming existing group ${EXISTING_GROUP} to docker"
      sed -i "s/^${EXISTING_GROUP}:/docker:/" /etc/group || error "Failed to rename group"
    else
      log "Docker group already exists with correct GID"
    fi
  fi
else
  log "Docker socket not found at /var/run/docker.sock"
fi

# Handle runtime UID/GID changes
if test -n "${PUID}" && test -n "${PGID}"
then
  log "Changing user/group IDs at runtime to ${PUID}:${PGID}"
  
  # Try to delete user and group, but don't fail if not found
  deluser appuser 2>/dev/null || true
  delgroup appuser 2>/dev/null || true

  # Create group and user with new IDs
  addgroup -g ${PGID} appuser || error "Failed to create appuser group with GID ${PGID}"
  adduser -D -u ${PUID} -G appuser -s /bin/sh appuser || error "Failed to create appuser with UID ${PUID}"

  # Set correct permissions on directories
  chown -R appuser:appuser /app /backups

  # Mirror host user groups for the PUID
  if test -n "${MIRROR_HOST_GROUPS}" && test "${MIRROR_HOST_GROUPS}" = "true"
  then
    log "Mirroring host user groups for PUID ${PUID}"
    
    # First check if host group file is available
    if test -e "/host/etc/group"
    then
      log "Using /host/etc/group for group discovery"
      
      # Create a unique username to look for in the group file based on PUID
      # This handles cases where the username in the container doesn't match the host
      HOST_USER=$(grep -l ":x:${PUID}:" /host/etc/passwd 2>/dev/null | xargs -r cat | cut -d: -f1)
      if test -n "${HOST_USER}"
      then
        log "Found host username for UID ${PUID}: ${HOST_USER}"
      else
        HOST_USER="backup"  # Fallback to a common name like 'backup'
        log "No host username found for UID ${PUID}, using fallback: ${HOST_USER}"
      fi
      
      # Find groups where:
      # 1. The group has UID as direct owner (3rd field) - ":x:$PUID:"
      # 2. The group has the username as a member (4th field) - ":$HOST_USER," or ",$HOST_USER," or ",$HOST_USER$"
      GROUP_LIST=$(grep -E "(:[^:]*:${PUID}:)|(:[^:]*:[^:]*:([^,]*,)*${HOST_USER}(,[^,]*)*$)" /host/etc/group)
      
      if test -n "${GROUP_LIST}"
      then
        log "Found groups for user ${HOST_USER} (${PUID}):"
        echo "${GROUP_LIST}" | while IFS=: read -r group_name group_pass group_id group_members
        do
          log "  - ${group_name} (GID: ${group_id})"
          
          # Skip the user's primary group if it matches PGID
          if test "${group_id}" = "${PGID}"
          then
            log "    Skipping primary group ${group_name}"
            continue
          fi
          
          # Create group if it doesn't exist
          if ! getent group ${group_id} > /dev/null
          then
            addgroup -g ${group_id} ${group_name} 2>/dev/null || 
              log "    Failed to create group ${group_name} (${group_id})"
          fi
          
          # Add user to group
          adduser appuser ${group_name} 2>/dev/null || 
            log "    Failed to add appuser to group ${group_name}"
        done
      else
        log "No additional groups found for UID ${PUID} or user ${HOST_USER}"
      fi
    else
      log "Could not find host group file - mount /etc/group as /host/etc/group to enable group mirroring"
    fi
  fi
fi

# Manually add specified groups if provided
if test -n "${ADDITIONAL_GROUPS}"
then
  log "Adding user to additional groups: ${ADDITIONAL_GROUPS}"
  for group_spec in $(echo "${ADDITIONAL_GROUPS}" | tr "," " ")
  do
    # Check if group specification includes GID (format: name:GID)
    if echo "${group_spec}" | grep -q ":"
    then
      group_name=$(echo "${group_spec}" | cut -d: -f1)
      group_id=$(echo "${group_spec}" | cut -d: -f2)
      # Create group with specific GID
      addgroup -g ${group_id} ${group_name} 2>/dev/null || log "Failed to create group ${group_name}"
    else
      group_name="${group_spec}"
    fi
    # Add user to group
    adduser appuser ${group_name} 2>/dev/null || log "Failed to add user to group ${group_name}"
  done
fi

# Add appuser to the docker group if it exists
if getent group docker > /dev/null
then
  log "Adding appuser to docker group"
  adduser appuser docker || error "Failed to add appuser to docker group"
fi

# Print the final user/group setup
log "Running as:"
su-exec appuser id

# Execute the command as appuser
log "Starting application with command: $@"
exec su-exec appuser "$@"
