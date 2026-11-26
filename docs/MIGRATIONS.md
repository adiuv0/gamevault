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
| `1.0.3` | `1.1.0` | Applies `m013` through `m016` | Automated tests cover the `m015` backfill of existing rows and its triggers, and the `m016` sign-in table |
| `1.1.0` | `1.2.0` | Applies `m017` | Automated fixture upgrades the shipped `1.1.0` schema; a test covers the background regroup `m017` queues |
| `1.2.0` | `1.2.1` | None | No schema change; the automated fixture confirms the `1.2.0` schema opens unchanged |
| `1.2.1` | `1.2.2` | None | No schema change |
| `1.2.2` | `1.2.3` | Applies `m018` | Automated fixture upgrades the shipped `1.2.2` schema; tests cover the new column and the background repair `m018` queues |
| `1.2.3` | `1.3.0` | Applies `m019` | Automated fixture upgrades the shipped `1.2.3` schema; tests cover the new column and the startup backfill |
| `1.3.0` | `1.3.1` | Applies `m020` | Automated fixture upgrades the shipped `1.3.0` schema; tests cover the background regroup `m020` queues |
| `1.3.1` | `1.4.0` | Applies `m021` | Automated fixture upgrades the shipped `1.3.1` schema; tests cover the revision triggers through the Timeline statistics cache |
| `1.4.0` | `1.4.1` | Applies `m022` | Automated fixture upgrades the shipped `1.4.0` schema; tests cover the capture time repair `m022` queues and the regroup behind it |
| `1.4.1` | `1.5.0` | Applies `m023` | Automated fixture upgrades the shipped `1.4.1` schema; tests cover deletes recording hashes and Sync/imports skipping them |

The `1.0.0` migrations add gallery indexes, logical capture groups, duplicate review,
viewer previews, and durable import/upload jobs to Docker `0.3.1`. They do not rewrite
or delete original screenshot files. Older upgrade paths also apply the prior Special K,
annotation-sanitization, and unused-placeholder-table migrations as needed.
Versions `1.0.1` and `1.0.2` add no migrations. Version `1.0.3` adds nullable HDR
classification metadata used by duplicate review. It does not alter capture groups or
read, rewrite, move, or delete original image files during migration; a subsequent
duplicate scan classifies older PNG assets from their headers.

Version `1.1.0` adds `m013` (game-merge redirects for in-flight uploads), `m014`
(the `logical_captures` view), and `m015`, which stores each capture's representative
asset in `screenshots.is_capture_primary`, backfills it once, and keeps it current with
triggers. `m015` touches only metadata; on a large library the one-time backfill and
index builds take a few seconds at first startup. `m016` adds the empty
`device_tokens` table for GameVault Sync sign-ins.

`m017` queues a duplicate scan that the server runs in the background after startup.
The scan pairs saved copies of one capture (for example Steam's JPG and Special K's
JXR) by changing only `screenshots.capture_group_id`; it never touches image files,
and every pairing can be undone with **Ungroup** in the viewer.

`m018` adds the nullable `screenshots.steam_posted_at` and queues a background repair
of Steam imports, which until `1.2.3` stored the time Steam posted a screenshot as the
time it was taken. After startup the server asks Steam's image servers for each
imported screenshot's original file name, which holds the capture time, using the
profile of the last Steam import. It changes only `screenshots.taken_at`, moves the
previous value into `steam_posted_at`, and downloads no images. Without internet
access, or for screenshots Steam does not list publicly, nothing changes; running a
Steam import again repairs those.

`m019` adds the nullable `games.accent_color`, a `#rrggbb` colour taken from each
game's cover art that the interface uses for highlights. Existing games are filled in
after startup by reading each cover once from the library folder; nothing is fetched
and no file is changed. Older releases ignore the extra column, so rolling back to
`1.2.3` needs no restore.

`m020` queues the same background scan as `m017`, after pairing learned to match
copies that have no capture time (files uploaded before times were read) by
picture alone when exactly one copy of another type fits. It changes only
`screenshots.capture_group_id`, so older releases read the new groups as they are,
rolling back to `1.3.0` needs no restore, and each pairing can be undone with
**Ungroup**.

`m021` adds a one-row `library_revision` table and triggers that bump it on any
insert, update or delete of a capture, game or annotation, so Timeline
statistics can be cached until something changes. It changes no existing data.
Older releases ignore the table, and the triggers keep working under them, so
rolling back to `1.3.1` needs no restore.

`m022` queues a background repair that reads the capture time recorded inside
files stored without one (an XMP packet from Special K or the Windows capture
path, or an older Special K PNG `Creation Time` chunk), then a fresh duplicate
scan that waits for it, so newly dated copies pair by time. It only fills
`screenshots.taken_at` where it was NULL, and an undated copy grouped with a dated
one takes that time. No schema change: older releases read the times as they
are, so rolling back to `1.4.0` needs no restore (the times stay filled in).

`m023` adds an empty `deleted_screenshots` table: the sha256 of each file deleted
in GameVault, so GameVault Sync and the Steam / Special K importers don't bring it
back. It changes no existing data, and older releases ignore the table, so rolling
back to `1.4.1` needs no restore (Sync would then upload deleted files again).

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
