# GameVault Migration And Recovery Notes

This document records supported upgrade paths, backup requirements, and conservative
rollback procedures. Migrations run automatically when GameVault starts.

## Supported Upgrade Paths

| From | To | Database work | Verification |
|---|---|---|---|
| `0.1.0` | `0.2.0` (unreleased) | Applies `m003` through `m011` | Automated fixture preserves games, screenshots, annotations, settings, visibility, and foreign keys |

The `0.2.0` migrations add Special K sessions, sanitized annotations, gallery indexes,
logical capture groups, duplicate review, viewer previews, and durable import/upload
jobs. They do not rewrite or delete original screenshot files. `m005` removes only the
unused placeholder tag tables that had no application feature attached to them.

## Before Upgrading

1. Let browser uploads finish.
2. Create and verify a live state bundle:

```bash
docker exec gamevault python -m backend.backup create /data/backups
docker exec gamevault python -m backend.backup verify /data/backups/<bundle-name>
```

3. For a fully point-in-time recovery set, stop the container and copy
   `GAMEVAULT_DATA_DIR` plus any separately mounted `GAMEVAULT_LIBRARY_DIR`.
4. Keep that pre-upgrade backup until the upgraded container has opened the library and
   completed its migrations successfully.

The live state bundle contains only a verified standalone database, the effective secret
key, and a checksum manifest. It does not contain original screenshots. A cold copy of
the data/library paths is the authoritative full backup.

## Restore A State Bundle

Verify the bundle while the current container is still running, then stop GameVault.
Preserve the current database files before replacing anything:

```bash
docker exec gamevault python -m backend.backup verify /data/backups/<bundle-name>
docker stop gamevault

cd /mnt/user/appdata/gamevault
mkdir -p pre-restore-safety
for file in gamevault.db gamevault.db-wal gamevault.db-shm .secret_key; do
  [ ! -e "$file" ] || mv "$file" pre-restore-safety/
done

cp backups/<bundle-name>/gamevault.db ./gamevault.db
cp backups/<bundle-name>/.secret_key ./.secret_key
docker start gamevault
```

Do not remove or replace `library/`, a separately mounted library path, or the thumbnail
cache during a state restore. If `GAMEVAULT_SECRET_KEY` is explicitly set in the
container configuration, it overrides `.secret_key`; restore the matching configured
value as well.

After startup, confirm the health endpoint, library counts, and several originals. Keep
`pre-restore-safety/` until the restored installation has been checked.

## Roll Back A Release

Rolling back an image tag does not reverse schema migrations. The conservative rollback
is to restore the pre-upgrade database-and-key bundle and run the previous image against
that matching state:

1. Stop the new container.
2. Move its current database, WAL, SHM, and key into a safety directory.
3. Restore the verified pre-upgrade bundle as described above.
4. Select the previous versioned container image.
5. Start GameVault and verify library counts and sample originals.

Never point an older release at a migrated database unless that exact downgrade path is
documented as supported. GameVault migrations are forward-only; recovery uses a backup,
not reverse SQL.
