#!/bin/sh
# Ensure data directories exist and are owned by the gamevault user
# This runs as root before dropping to the non-root user

mkdir -p /data/library

# Fix ownership on mounted volumes
chown -R gamevault:gamevault /data

# Drop to gamevault user and exec the main process
exec gosu gamevault "$@"
