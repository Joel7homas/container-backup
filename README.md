# Overview

This tool is intended as a better-than-nothing stop-gap for automatic container discovery and backup in a lab environment.

## Background
I tend to install and configure applications in Docker faster than I get around to configuring those applications to be backed up off site. Still, I want to feel like I *probably* have a good backup of my data for the applications I'm testing and debugging but haven't got around yet to making fully production-ready with security hardening, backups, etc.

## Strategy

The "better-than-nothing" strategy is a nightly process that can be described as follows.

For each stack configured in Portainer that has not been individually configured for proper backups:
- Discover databases running in Docker containers and export their data to `/mnt/backups`
- Tar and compress all non-database data from `/mnt/docker` into `/mnt/backups`
- Replicate the contents of `/mnt/backups` to off-site storage
 
**NOTE** - If it isn't obvious yet, this is not intended to be a replacement for a proper backup tool. It's probably better than working without a safety net in the lab, but production applications deserve individual attention.

## Requirements
- **Python** - The database discovery and backup script is written in python.
- **Portainer** - The discovery process uses the Portainer API to pull credentials from environment variables stored in stack.env files
- **Docker** - Podman might work, but this was built on Docker.
  
