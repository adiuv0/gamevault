# Maintenance Review Fixes

## Upgrade and preservation

- The existing SQLite database and original images remain compatible. New migrations
  add merge redirects and a logical-capture view; they do not rewrite originals.
- Back up the database and application state before deployment, following
  [MIGRATIONS.md](MIGRATIONS.md). Original-image backups remain separate.
- Existing login tokens are invalidated once by this upgrade. Log in again; CLI
  clients also need a fresh token. Subsequent password changes revoke all old tokens.
- Public galleries and the default visibility of new games are unchanged.

## Large HDR uploads

| Setting | Default | Meaning |
| --- | --- | --- |
| `GAMEVAULT_MAX_UPLOAD_SIZE_MB` | `512` | MiB per individual file |
| `GAMEVAULT_MAX_UPLOAD_BATCH_SIZE_MB` | `8192` | MiB for the entire multipart request |
| `GAMEVAULT_MAX_UPLOAD_FILES` | `200` | Files per batch |

These are independent, configurable limits. A batch may exceed the per-file limit
as long as each file fits and the batch remains within its own limit. Multi-MB HDR
screenshots are comfortably below the defaults. Image dimensions and decoder
safety checks still apply; file size alone does not determine decoded memory use.
An explicitly configured older limit (for example, 50 MiB) is not overridden.

Authentication is checked before body parsing. File and total-request limits are
enforced while parsing, including chunked uploads without Content-Length. Partial
spools are closed on failure. The UI shows configured limits and server errors.
Keep sufficient temporary/staging disk space for concurrent uploads. A reverse
proxy may impose a lower body limit; configure it separately for the desired batch
size plus multipart overhead. No proxy configuration is changed by this update.

## Integrity and behavior

- Production SPA paths are resolved and checked against the build directory.
  Encoded traversal and escaping symlinks cannot serve repository files.
- SQLite connections belong to asyncio tasks, not the whole application. An import
  rollback cannot undo a different request's work.
- Game merges commit metadata atomically. Files, including covers and untracked
  files, stay in their existing folders. Those folders are still part of the library;
  do not delete them after a merge. Pending upload jobs move to the target and
  in-flight screenshot inserts resolve the old game ID to the merged target.
- Non-identical new screenshots are no longer silently grouped by perceptual hash
  or filename-derived source keys. HDR/SDR suggestions remain in Duplicate Review;
  exact-byte duplicate skipping is unchanged. Existing groups remain intact.
- The viewer can ungroup its selected variant. Original files, favorites, notes,
  and share links are preserved, and the separated pair is marked dismissed.
- Search, timeline, and settings use logical-capture counts rather than counting
  HDR and SDR variants separately. Search can match any member of a capture.
- Thumbnail and preview names are content-addressed and written atomically.
  Same-stem JPG/PNG/JXR files no longer overwrite one another's derivatives.
- RGB16 PNG decoding retains all 16 bits using imagecodecs. High-bit-depth PNGs
  continue through the existing tone-mapping pipeline; comprehensive color-profile
  and HDR-transfer-function interpretation is not introduced here.
- Viewer shortcuts ignore editable fields. Unsaved notes survive pane toggles,
  capture navigation, and closing/reopening the viewer within the same page session.
  Drafts are in memory only: reloading or closing the tab discards unsaved drafts.
- Filesystem size scans run off the event loop, are coalesced, and are cached for
  five minutes. The first settings request still waits for its initial scan.

## Existing cache repair

After deployment, run **Settings > Repair image cache**. This resumable operation
rebuilds old or missing thumbnail/preview bundles with collision-safe filenames.
It also refreshes HDR classification for previously misidentified RGB16 PNG files.
It only changes disposable derivatives and database cache paths, not originals.
Old cache files are retained rather than bulk-deleted; missing originals are counted
as job errors. The repair is explicit because it can be expensive for large libraries.

## Verification

- Local validation: 324 backend tests and six desktop/mobile browser tests passed;
  frontend lint/build, Python dependency audit, and npm dependency audit passed.
- Backend tests include traversal, streaming upload boundaries, credential
  revocation, transaction isolation, merge interruption, ungrouping, RGB16 decoding,
  and nonblocking storage statistics.
- Playwright runs desktop and mobile regressions for annotation editing, failed
  saves, navigation, deep scrolling, returning to earlier images, and date sorting.
- Frontend CI runs lint, production build, dependency audit, and browser tests.
  Run locally with `npm run test:e2e` after `npx playwright install chromium`.

These changes do not constitute a new deployment or a new release by themselves.
