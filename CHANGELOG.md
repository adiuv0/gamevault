# Changelog

All notable changes to GameVault will be tracked in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project loosely follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- JPEG XR (HDR) and TIFF screenshots now open at full resolution. Browsers can't
  show those formats, so the viewer used to stop at the 1920px preview. It now
  shows the preview at once and swaps in a full-size tone-mapped JPEG
  (`GET /api/screenshots/{id}/full`). New imports make that render alongside the
  thumbnails from the same decode, so it costs only a JPEG encode and ~0.5-1 MB
  of cache per 4K capture. **Settings > Repair image cache** renders existing
  captures; until then they are rendered on first view. Public galleries
  and share links get the same render; share links used to serve the raw `.jxr`,
  which browsers and link previews couldn't display. Download still gives the
  original file.

### Changed

- A JXR and a PNG/JPEG with the same file name, resolution and image, captured
  within five minutes of each other, are now paired as one capture on import
  instead of waiting on the Duplicates page, so the viewer's JXR/PNG toggle
  appears on its own. **Scan library** on the Duplicates page pairs existing
  ones. Differently named matches still go to review, and dismissed pairs stay
  apart.
- HDR tone-mapping encodes sRGB through a lookup table, which cuts that step from
  about 1.3 s to 0.16 s on a 4K capture. Thumbnails and previews of JPEG XR
  imports are made correspondingly faster; output differs by at most one 8-bit
  code on about 0.1% of pixels.

## [1.1.0] - 2026-10-11

### Added

- GameVault Sync is now a desktop app instead of a Python script: a Windows
  installer and portable `.exe` (built by the `Desktop app` workflow and attached
  to `v*` releases), a tray icon, start with Windows, and a window that signs in
  with your password. It finds Steam and Special K by itself, including non-Steam
  games added to Steam, checks for new screenshots about once a minute, reads only
  files it hasn't seen before, and waits for a click before the first big upload.
  `cli/gamevault_sync.py` and its options still work and start the app.
- Sync-app sign-ins are per-PC device tokens (`m016`), listed under
  **Settings > GameVault Sync** with a **Sign out** button. They don't expire, can
  only reach the upload endpoints, and are all revoked when the password changes.
  Signing in again from the same install replaces its old token; two PCs with the
  same name keep their own. `POST /api/auth/devices/pair`, `GET/POST /api/auth/devices`,
  `DELETE /api/auth/devices/{id}` and `DELETE /api/auth/devices/current`.
- `GET /api/health` includes `instance`, a random id per database, so the sync app
  can tell a new address for the same server from a different server.
- `POST /api/upload/sync` reports which files were skipped as duplicates and which
  were refused, and accepts a `taken_at_hint` date used when a file has no EXIF date.
- `GET /api/games/by-steam-appid/{id}` accepts `?name=`, used instead of an
  `App 12345` placeholder when the server can't reach the Steam store.

### Changed

- Timeline and search are roughly 600x and 900x faster on an 11k-screenshot
  library (3.4 s to 6 ms, 2.0 s to 2 ms). Each capture's representative asset is
  now stored and indexed (`m015`) instead of being ranked across the whole table
  on every query, and the timeline loads a page of days in three queries.
- The admin UI no longer waits on a full library disk scan when it opens. Token
  checks use `/api/auth/verify`, and storage sizes are served from the last scan
  while a fresh one runs in the background.
- The viewer shows the cached preview immediately, swaps in the original once it
  has downloaded, and preloads the neighbouring previews.
- Steam and Special K imports process screenshots concurrently
  (`GAMEVAULT_IMPORT_CONCURRENCY`, default 2) and decode each image once. JXR
  dimensions are read from the file header, and JPEG previews decode at reduced scale.
- Uploads and imports run in separate background lanes, so a long import no longer
  blocks browser uploads.
- Database connections are pooled and use `synchronous=NORMAL`; the security-headers
  middleware is pure ASGI. Pages are code-split per route.

### Fixed

- A single error no longer stops all background jobs; lanes log, back off, and continue.
- A file that crashes the process during import is failed after three attempts
  instead of being retried on every restart.
- Steam imports retry rate-limited and failed requests, fail instead of silently
  truncating when a later page errors, validate downloads as images, and check the
  host allowlist on every redirect.
- Cancelling an import during its last game now reports it as cancelled.
- Search thumbnails load when authentication is enabled, and stale search or timeline
  responses can no longer replace newer ones.
- The share panel can no longer show or delete another screenshot's link after navigating.
- Deleting a screenshot removes its database row before its files.
- Replaced covers appear immediately instead of after the 24-hour cache expires.
- Concurrent uploads that pick the same filename can no longer overwrite each other.
- IGDB and SteamGridDB lookups escape game names.

### Security

- PyJWT is upgraded to 2.15.1, which fixes several token-forgery and
  authentication-bypass advisories in 2.13.0. Frontend build tooling picks up patched
  `brace-expansion` and `source-map-js`.

### Removed

- The Settings page's **Sync Token** (a copy of the 30-day browser session) is
  replaced by the sign-in above.

## [1.0.3] - 2026-08-22

### Added

- Private and public game galleries can sort screenshots by capture date with
  newest-first and oldest-first controls that preserve cursor pagination.

### Fixed

- Duplicate review now proposes only complementary HDR/SDR assets with matching
  dimensions and strong filename or capture-time evidence. Visually similar
  same-format screenshots no longer flood the pending review queue.
- Automatic perceptual grouping now requires an HDR/SDR relationship, preventing
  nearby same-scene screenshots from being silently counted as one capture.
- HDR classification now covers JXR/WDP and detected 16-bit HDR PNG sources.

## [1.0.2] - 2026-08-22

### Fixed

- Virtualized screenshot grids now use the signed-in application's actual
  scrollable content panel. Returning toward the top after loading a large
  gallery no longer leaves a blank spacer where screenshots should be.
- Public galleries continue to virtualize against the browser window, while
  pagination observes the same scroll surface used by each gallery.

## [1.0.1] - 2026-08-22

### Fixed

- Opening a screenshot in either the private or public gallery now loads the
  full-resolution original. Browser-incompatible originals still fall back to
  their generated preview.
- Private and public galleries now use a bottom-of-list observer plus a visible
  load-more fallback so virtualized browsing cannot silently stall after the
  first batch.

## [1.0.0] - 2026-08-22

GameVault 1.0 is the first Git-tagged release. Docker `0.2.0`, `0.3.0`, and
`0.3.1` were published without matching Git tags or changelog cuts, so this section
preserves the cumulative development record from `0.1.0`. Compared with Docker
`0.3.1`, the major additions are logical duplicate handling, large-library scaling,
restart-safe imports and uploads, verified recovery backups, and continuous security
scanning. Existing installs upgrade in place; migrations run automatically on startup
and never delete original screenshot files.

### Added

- Logical capture groups and perceptual dHash fingerprints. HDR/SDR or other
  source variants can share one capture identity, and game screenshot totals
  count captures rather than physical files.
- Resumable duplicate scanning for existing libraries, with durable progress,
  conservative same-game candidate rules, and a review queue for grouping or
  dismissing visually similar variants without deleting originals.
- Game and public galleries now paginate logical captures instead of physical
  files. Grouped captures render once with an in-viewer variant selector;
  favorites and annotations apply across the capture.
- Responsive admin navigation for phone-sized screens.
- Configurable SSD-friendly thumbnail cache (`GAMEVAULT_THUMBNAIL_DIR`) with
  backward-compatible legacy reads and a Settings migration action.
- Disposable 1920px viewer previews generated alongside thumbnails from the same image
  decode, plus a durable Settings backfill for existing or cache-wiped libraries.
- Reproducible metadata-only benchmarks for 1,000 and 10,000 logical captures.
- Bounded runtime performance diagnostics for gallery queries, streamed media, image
  processing, and imports, with rate-limited slow-operation container warnings and an
  authenticated Settings summary/reset view. Diagnostics retain no paths, filenames, or
  request data.
- Restart-safe Steam and Special K imports backed by an encrypted SQLite job queue.
  Interrupted jobs resume on startup, terminal progress remains available after restart,
  and completed or cancelled jobs discard their encrypted payloads.
- Restart-safe browser uploads with persistent request-body staging, per-file replay
  state, idempotent recovery across both file-copy/database commit windows, and
  reconnectable progress snapshots.
- Verified live state-backup CLI (`python -m backend.backup`) using SQLite's online
  backup API. Bundles include committed WAL state and the effective secret key, reject
  active browser uploads, verify checksums/integrity/foreign keys, and publish atomically.
- Release-recovery coverage for every published schema from `0.1.0` through Docker
  `0.3.1`, active-WAL restore, tamper detection, and synchronized versions.
- GitHub Actions CI for backend tests, frontend lint/build, and the Docker image, plus
  Python/npm dependency audits and a fixable high/critical container vulnerability gate.
  The full workflow also runs weekly so newly published advisories are detected.
- **Game merge.** Game admin page now has a "Merge" button that opens a
  modal listing every other game, lets you pick a target, and moves all
  screenshots (DB rows + files + thumbnails) into it. Annotations and
  share-links travel with their screenshots automatically. The target's
  cover is preserved; the source game and its on-disk folder are removed
  after the merge. Use this to consolidate accidental duplicates from
  the Special K importer (e.g. `Cyberpunk 2077` next to
  `Cyberpunk 2077: Phantom Liberty`).
- **HDR/SDR pair toggle in the gallery viewer.** When Special K writes
  a `.jxr` and a `.png` of the same moment, the viewer now shows a
  toggle button (and `h` keyboard shortcut) to flip between the two
  variants of the same screenshot.
- HDR JXR upload pipeline. JXR files are decoded with `imagecodecs`,
  tone-mapped to SDR with a configurable algorithm (Reinhard / ACES /
  hard clip), and SDR JPEG thumbnails are generated for the gallery.
  The original HDR file is preserved on disk for download. 16-bit HDR
  PNGs go through the same pipeline.
- Special K filesystem importer (web UI page at `/import/specialk` plus
  CLI `--mode specialk`). Each top-level subfolder is treated as a
  game; folder names are cleaned client-and-server-side so
  `Cyberpunk2077` becomes `Cyberpunk 2077`.
- `POST /api/games/by-name` endpoint (get-or-create by display name).
  Used by the Sync CLI for the Special K workflow so a folder named the
  same as a Steam-imported game merges into one library entry.
- `Settings` page gains a "Special K & HDR" section: path, tone-map
  algorithm dropdown, exposure slider.
- `GAMEVAULT_SPECIALK_ALLOWED_ROOTS` env var to gate the importer
  (required when `GAMEVAULT_DISABLE_AUTH=true`).
- Reference documentation set under `docs/`: `USER_GUIDE`,
  `API_REFERENCE`, `CONFIGURATION`, `ARCHITECTURE`, `RELEASING`.

### Changed

- Gallery cards use responsive 300/800px sources, private media receives browser
  caching headers, and media requests use narrow database lookups.
- Admin and public viewers load bounded previews by default; download links continue to
  serve untouched full-resolution originals.
- Gallery list responses now contain only card fields; full logical-capture and variant
  metadata is fetched when a viewer opens, reducing the 50-card benchmark payload by
  83% even after adding opaque media revisions.
- Authenticated thumbnail, preview, and original URLs now carry opaque revisions. Matching
  revisions receive private immutable browser caching, while stale and public URLs retain
  conservative one-day policies so regenerated previews refresh and public access remains
  revocable.
- Admin and public game galleries now use opaque cursor pagination and a shared
  window-scrolling virtual grid. Existing numbered-page API clients remain compatible,
  while long browsing sessions keep only the visible card window mounted.
- Imports decode each source once for both thumbnail sizes, perform image work
  outside the async request loop, and recalculate game statistics once per batch.
- Added composite gallery indexes for game/date and game/filename ordering.
- **`get_game_by_name` is now case-insensitive.** Prevents the Special K
  importer (and manual create) from accidentally creating a duplicate
  when an existing game with the same name in different casing already
  exists. Matches `Cyberpunk 2077` and `cyberpunk 2077` as the same row.
- Sync CLI (`cli/gamevault_sync.py`) supports both Steam and Special K
  sources via `--mode {steam,specialk,both}`; default `steam` for
  backward compatibility. GUI gains a Mode dropdown plus a second path
  field.
- Sync CLI auth token is now stored in the OS keyring when the optional
  `keyring` package is installed (Credential Manager / Keychain /
  Secret Service). Falls back to `~/.gamevault_sync.json` plaintext
  otherwise with a one-time stderr warning.
- Sync CLI shows a friendly recovery message on HTTP 401 instead of the
  raw httpx error.
- `requirements.txt` pinned to exact versions for reproducible Docker
  builds. `pyproject.toml` keeps `>=` lower bounds for source/dev
  installs.
- Vulnerable Python and npm dependencies were upgraded to fixed releases. JWT handling
  moved from `python-jose` to compatible HS256 tokens from PyJWT, removing an affected
  transitive ECDSA package that had no patched release.
- Production image builds apply available Debian base-image security updates before
  installing runtime dependencies; the release image must pass the container gate.
- SQL `ORDER BY` fragments lifted from inline dict literals to
  module-level allowlist constants (`GAME_SORT_CLAUSES`,
  `SCREENSHOT_SORT_CLAUSES`, `SEARCH_SORT_CLAUSES`,
  `SEARCH_LIST_ALL_SORT_CLAUSES`). Behavior unchanged but intent is now
  explicit and tested.
- HDR tone-map settings cached in memory; previously every thumbnail
  re-opened a SQLite connection. Cache is invalidated when
  `PUT /api/settings/preferences` writes new values.

### Security

Closes every finding in `docs/SECURITY_AUDIT_2026-04-27.md` (GV-001
through GV-014).

- **GV-001** (High): annotation Markdown rendering hardened. Markdown
  is rendered with raw HTML disabled and the result is sanitized with
  `nh3` against a strict tag/attribute allowlist; `javascript:` and
  `data:` URI schemes are dropped. The `m004_resanitize_annotations`
  migration re-renders every existing row so dormant payloads from
  before the fix are cleaned automatically.
- **GV-003** (High): upload temp-file paths are uuid-based. The
  user-supplied filename is no longer joined into any path — only kept
  as a sanitized display string for progress events.
- **GV-004** (High): uploads are streamed in 1 MB chunks with a hard
  byte cap; oversize requests abort early without buffering. Pillow
  `MAX_IMAGE_PIXELS` set to 144 MP to bound decompression work.
- **GV-006** (Medium): `change-password` requires a valid JWT and has
  its own per-IP rate limiter, separate from login.
- **GV-002** (Medium): public gallery responses use slim
  `PublicScreenshot` / `PublicGame` Pydantic models. Internal fields
  (`file_path`, `sha256_hash`, `exif_data`, `steam_screenshot_id`,
  `source`, `folder_name`, etc.) no longer leak to unauthenticated
  viewers.
- **GV-005** (Medium): `?token=` query-string auth restricted to a
  hardcoded allowlist (SSE progress endpoints + image/thumb/cover
  routes). Silently ignored on every other path.
- **GV-007** (Medium): `/share/{token}/image` now serves image bytes
  directly. The OG `<meta property="og:image">` tag points at this
  route so Discord/Slack/Twitter previews work without exposing
  `/api/screenshots`.
- **GV-008** (Medium): new `safe_library_path()` helper rejects
  traversal/absolute paths in every file-serving route. Defense in
  depth against poisoned DB rows.
- **GV-009** (Medium): `SecurityHeadersMiddleware` adds
  `Content-Security-Policy`, `X-Frame-Options: DENY`,
  `Referrer-Policy`, and `Permissions-Policy` to every response. CSP
  is route-aware (stricter for `/share/*`).
- **GV-010** (Medium): Special K importer gated by
  `GAMEVAULT_SPECIALK_ALLOWED_ROOTS`. When auth is disabled and no
  allowlist is configured, the endpoint refuses with HTTP 403.
- **GV-011** (Medium): CLI auth token defaults to OS keyring storage.
- **GV-012** (Medium): direct Python deps pinned to exact versions in
  `requirements.txt`. `docker-compose.yml` documents how to switch to
  an immutable digest pin in production.
- **GV-013** (Low): SQL sort fragments are now explicit module-level
  allowlists with named defaults; tests verify malicious sort tokens
  fall back safely.
- **GV-014** (Low): `docs/CONFIGURATION.md` documents login rate
  limiting behind a reverse proxy, the `entrypoint.sh` chown behavior,
  and the three-mode Special K safety table.

### Removed

- Unused `tags` and `screenshot_tags` tables (m005 migration). They
  were created in m001 but never wired up to a router/service/UI; if
  per-screenshot tagging is needed later it can be added back via a
  new migration.

### Migrations

Run automatically on container startup:

| ID | Effect |
|---|---|
| `m003_specialk` | Adds `specialk_import_sessions` table |
| `m004_resanitize_annotations` | Re-renders every annotation through the new sanitizer |
| `m005_drop_tags` | Drops the unused `tags` + `screenshot_tags` tables |
| `m006_gallery_indexes` | Adds gallery and import lookup indexes |
| `m007_capture_groups` | Adds logical capture groups and perceptual hashes |
| `m008_duplicate_review` | Adds durable maintenance jobs and duplicate review candidates |
| `m009_preview_cache` | Tracks disposable browser-friendly large previews |
| `m010_durable_import_jobs` | Adds the encrypted, restart-safe Steam and Special K job queue |
| `m011_durable_upload_jobs` | Adds restart-safe browser-upload staging and replay state |

## [0.3.1] - 2026-05-01

Docker-only release. Added game merge and the HDR/SDR gallery variant toggle.

## [0.3.0] - 2026-04-30

Docker-only release. Closed the 14-finding security audit, cached HDR tone-map
settings, and removed the unused placeholder tag tables.

## [0.2.0] - 2026-04-27

Docker-only release. Added HDR/JXR processing, Special K imports, the dual-source
sync CLI, and the initial documentation set.

## [0.1.0] - 2026-02-11

Initial release.

### Added

- FastAPI + SQLite (WAL + FTS5) backend
- React 18 + TypeScript + Vite + Tailwind v4 frontend
- Steam Community import (HTML scraping + Web API hybrid) with cookie
  support for private screenshots
- Manual upload with drag-and-drop, automatic thumbnail generation,
  EXIF date extraction
- Per-game library, timeline view, full-text search with FTS5 + BM25
  ranking
- Markdown annotations (rendered server-side)
- Public share links with OpenGraph + Twitter Card meta tags
- Public read-only gallery (no auth) for `is_public` games
- Steam Store API + SteamGridDB + IGDB metadata cascade
- Single-user JWT authentication with bcrypt password hashing
- Standalone Sync CLI for uploading local Steam screenshots
- Multi-stage Docker build, Unraid template, docker-compose example
