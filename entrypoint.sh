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

# Create a group with fallback to groupadd if addgroup fails
create_group_with_fallback() {
    local group_name="$1"
    local group_id="$2"
    
    # Try addgroup first (Alpine default)
    if addgroup -g ${group_id} ${group_name} 2>/dev/null; then
        return 0
    else
        log "addgroup failed, trying groupadd for group ${group_name} (${group_id})"
        # Check if groupadd exists
        if command -v groupadd >/dev/null 2>&1; then
            if groupadd -g ${group_id} ${group_name} 2>/dev/null; then
                return 0
            fi
        fi
    fi
    
    return 1
}

# Add user to group with fallback methods
add_user_to_group() {
    local username="$1"
    local groupname="$2"
    
    # Try adduser method (Alpine)
    if adduser ${username} ${groupname} 2>/dev/null; then
        return 0
    else
        # Try usermod method if available
        if command -v usermod >/dev/null 2>&1; then
            log "adduser failed, trying usermod for group ${groupname}"
            if usermod -a -G ${groupname} ${username} 2>/dev/null; then
                return 0
            fi
        fi
    fi
    
    return 1
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
    create_group_with_fallback docker ${DOCKER_GROUP_GID} || error "Failed to create docker group"
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

  # Add NFS-specific groups if they exist
  # These typically need to be added regardless of host group membership
  for gid in 8 988 999 1000 1001 2999 3000 3001 3002 3003 3004 3005 3006 3007 3008 3009 3010 3011 3012 3013 8675309; do
    group_name=$(getent group $gid 2>/dev/null | cut -d: -f1)
    if test -n "$group_name"; then
      log "Adding user to NFS-important group $group_name (GID: $gid)"
      add_user_to_group appuser $group_name
    else
      # Try to create the group with numeric name if it doesn't exist
      if create_group_with_fallback "group_$gid" $gid; then
        log "Created numeric group group_$gid (GID: $gid)"
        add_user_to_group appuser "group_$gid"
      fi
    fi
  done

  # Mirror host user groups for the PUID
  if test -n "${MIRROR_HOST_GROUPS}" && test "${MIRROR_HOST_GROUPS}" = "true"
  then
    log "Mirroring host user groups for PUID ${PUID}"
    
    # First check if host group file is available
    if test -e "/host/etc/group" && test -e "/host/etc/passwd"
    then
      log "Using host group and passwd files for group discovery"
      
      # Get host username for UID
      HOST_USER=$(grep "^[^:]*:x:${PUID}:" /host/etc/passwd 2>/dev/null | cut -d: -f1)
      if test -n "${HOST_USER}"
      then
        log "Found host username for UID ${PUID}: ${HOST_USER}"
      else
        HOST_USER="backup"  # Fallback to a common name like 'backup'
        log "No host username found for UID ${PUID}, using fallback: ${HOST_USER}"
      fi

      # Check for user 'backup' in groups even if it's not the host user
      if test "${HOST_USER}" != "backup"; then
        BACKUP_USER="backup"
        log "Also checking for group membership of user: ${BACKUP_USER}"
      fi
      
      # Find groups where the user is a member - fixed regex
      GROUP_LIST=$(grep -E ".*:.*:[0-9]+:.*(^|,)${HOST_USER}(,|$)" /host/etc/group)
      if test -n "${BACKUP_USER}"; then
        BACKUP_GROUPS=$(grep -E ".*:.*:[0-9]+:.*(^|,)${BACKUP_USER}(,|$)" /host/etc/group)
        if test -n "${BACKUP_GROUPS}"; then
          GROUP_LIST="${GROUP_LIST}"$'\n'"${BACKUP_GROUPS}"
        fi
      fi
      
      if test -n "${GROUP_LIST}"
      then
        log "Found groups with user as member:"
        echo "${GROUP_LIST}" | sort | uniq | while IFS=: read -r group_name group_pass group_id group_members
        do
          log "  - ${group_name} (GID: ${group_id})"
          
          # Skip the user's primary group if it matches PGID
          if test "${group_id}" = "${PGID}"
          then
            log "    Skipping primary group ${group_name}"
            continue
          fi
          
          # Check if a group with this GID already exists but with different name
          EXISTING_GROUP=$(getent group ${group_id} 2>/dev/null | cut -d: -f1)
          if test -n "${EXISTING_GROUP}" && test "${EXISTING_GROUP}" != "${group_name}"
          then
            log "    Group with GID ${group_id} already exists as '${EXISTING_GROUP}'"
            log "    Adding user to existing group ${EXISTING_GROUP}"
            add_user_to_group appuser ${EXISTING_GROUP} || log "    Failed to add user to group ${EXISTING_GROUP}"
            continue
          fi
          
          # Create group if it doesn't exist
          if ! getent group ${group_id} > /dev/null
          then
            if create_group_with_fallback ${group_name} ${group_id}; then
              log "    Created group ${group_name} with GID ${group_id}"
            else
              log "    Failed to create group ${group_name} with GID ${group_id}"
              continue
            fi
          fi
          
          # Add user to group
          if add_user_to_group appuser ${group_name}; then
            log "    Added appuser to group ${group_name}"
          else
            log "    Failed to add appuser to group ${group_name}"
          fi
        done
      else
        log "No additional groups found for UID ${PUID} or user ${HOST_USER}"
      fi
    else
      log "Could not find host group or passwd files - mount /etc/group as /host/etc/group and /etc/passwd as /host/etc/passwd"
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
      if create_group_with_fallback ${group_name} ${group_id}; then
        log "Created group ${group_name} with GID ${group_id}"
      else
        log "Failed to create group ${group_name} with GID ${group_id}"
        continue
      fi
    else
      group_name="${group_spec}"
    fi
    
    # Add user to group
    if add_user_to_group appuser ${group_name}; then
      log "Added user to group ${group_name}"
    else
      log "Failed to add user to group ${group_name}"
    fi
  done
fi

# Add appuser to the docker group if it exists
if getent group docker > /dev/null
then
  log "Adding appuser to docker group"
  add_user_to_group appuser docker || error "Failed to add appuser to docker group"
fi

# Install additional packages if needed
if test -n "${INSTALL_PACKAGES}" && test "${INSTALL_PACKAGES}" != "none"
then
  log "Installing additional packages: ${INSTALL_PACKAGES}"
  apk update && apk add --no-cache ${INSTALL_PACKAGES}
fi

# If this is an NFS environment, set NFS_MODE to "true"
if test -n "${NFS_MODE}" && test "${NFS_MODE}" = "true"
then
  log "NFS mode enabled - using more aggressive permission handling"
  
  # In NFS mode, set umask to ensure files are created with relaxed permissions
  umask 0002
  
  # Optionally, we can try remounting with different options
  # This would require additional privileges
fi

# Print the final user/group setup
log "Running as:"
su-exec appuser id

# Execute the command as appuser
log "Starting application with command: $@"
exec su-exec appuser "$@"
