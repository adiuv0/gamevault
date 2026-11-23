#!/bin/sh
# Ensure data directories exist and are owned by the gamevault user
# This runs as root before dropping to the non-root user

mkdir -p /data/library

# Fix ownership on mounted volumes. A recursive chown walks every file in the
# library, which on Unraid spins up the array disks on each start, so it only
# runs when the top-level folders aren't ours yet (first start, or a volume
# copied in as another user). GAMEVAULT_FORCE_CHOWN=true forces the full pass.
if [ "${GAMEVAULT_FORCE_CHOWN:-false}" = "true" ] \
    || [ -n "$(find /data /data/library -maxdepth 0 ! -user gamevault 2>/dev/null)" ]; then
    chown -R gamevault:gamevault /data
else
    # Cheap: any top-level folder (backups, cache, ...) that isn't ours yet.
    find /data -mindepth 1 -maxdepth 1 ! -user gamevault -exec chown -R gamevault:gamevault {} +
fi

# Drop to gamevault user and exec the main process
exec gosu gamevault "$@"
