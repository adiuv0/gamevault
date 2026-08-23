# GameVault Migration And Recovery Notes

This document records supported upgrade paths, backup requirements, and conservative
rollback procedures. Migrations run automatically when GameVault starts.

## Supported Upgrade Paths

| From | To | Database work | Verification |
|---|---|---|---|
| `0.1.0` | `1.0.3` | Applies `m003` through `m012` | Automated fixture preserves games, screenshots, annotations, settings, visibility, and foreign keys |
| Docker `0.2.0` | `1.0.3` | Applies `m004` through `m012` | Automated fixture preserves the same state from the shipped schema |
| Docker `0.3.0` / `0.3.1` | `1.0.3` | Applies `m006` through `m012` | Automated fixtures cover both published image tags |
| `1.0.0` | `1.0.3` | Applies `m012` | Automated fixture confirms the existing capture schema remains intact |
| `1.0.1` | `1.0.3` | Applies `m012` | Automated fixture confirms the existing capture schema remains intact |
| `1.0.2` | `1.0.3` | Applies `m012` | Automated fixture confirms the existing capture schema remains intact |

The `1.0.0` migrations add gallery indexes, logical capture groups, duplicate review,
viewer previews, and durable import/upload jobs to Docker `0.3.1`. They do not rewrite
or delete original screenshot files. Older upgrade paths also apply the prior Special K,
annotation-sanitization, and unused-placeholder-table migrations as needed.
Versions `1.0.1` and `1.0.2` add no migrations. Version `1.0.3` adds nullable HDR
classification metadata used by duplicate review. It does not alter capture groups or
read, rewrite, move, or delete original image files during migration; a subsequent
duplicate scan classifies older PNG assets from their headers.

## Before Upgrading

1. Let browser uploads finish.
2. When upgrading from Docker `0.3.1` or earlier, stop the old container and take an
   Unraid snapshot or cold copy of its appdata plus any separately mounted library path.
   Those images do not contain the backup command introduced in `1.0.0`.
3. For later releases, create and verify a live state bundle before upgrading:

```bash
docker exec gamevault python -m backend.backup create /data/backups
docker exec gamevault python -m backend.backup verify /data/backups/<bundle-name>
```

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
