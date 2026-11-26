# Changelog

All notable changes to GameVault will be tracked in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project loosely follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.4.0] - 2026-11-26

The Timeline now tells you how you play, not only what you captured.

### Added

- **Play sessions.** Each day on the Timeline is split into sessions: a run of
  one game's captures with no gap longer than 45 minutes. A session shows its
  game, start and end time, length, capture and HDR counts, and up to 12
  thumbnails spread from its first capture to its last, edged in the game's
  cover accent. Length is the time between the first and last capture, not
  time played.
- **Stats strip** for a year or all time, following the game filter:
  captures, days with captures, current and best streak, busiest day, play
  sessions with the longest one, and the share of captures with an HDR file.
- **Capture calendar.** A heatmap of captures per day for the chosen year (or
  the last 53 weeks). Clicking a day jumps the Timeline to it; **Back to
  latest** returns.
- **Most captured games** for the range, in each game's accent colour.
- **On this day**: captures from today's date in earlier years, when there
  are any.
- **When you capture**: captures by hour of day, read as each capture's own
  clock wrote it (the time zone question from the 1.2.1 audit is still open,
  so treat it as roughly right).
- `GET /api/timeline/insights` serves all of the above. Each day of
  `GET /api/timeline` gains `sessions`, `session_count` and `session_minutes`.

### Changed

- A Timeline day now sends up to 12 previews per session instead of its
  newest 50 captures, so a long evening shows its beginning as well as its
  end.
- Statistics count logical captures only, like the rest of the app. They read
  the existing per-day index and are cached until a capture, game or
  annotation changes (migration `m021`, which adds only a revision counter).
  At 10,000 captures a cache miss takes about 40 ms and a hit well under one.

## [1.3.1] - 2026-11-26

### Fixed

- **Screenshot counts no longer count one press twice.** Counts already treated
  a grouped JPG + JXR as one capture, but copies uploaded without a capture time
  (their names hold the upload time instead) were never grouped, so a game with
  Steam JPGs and Special K JXRs of the same shots showed close to double. Such
  copies now pair with the one capture of another type whose picture matches,
  only when that match is unambiguous both ways. A background scan
  (migration `m020`, which only queues it) regroups existing libraries after
  the update; game, sidebar, Timeline and Settings totals drop as it runs.

## [1.3.0] - 2026-11-26

A new look. The interface no longer borrows a code-hosting site's dark theme:
games supply the colour. Includes a small database migration (`m019`, one new
column that older releases ignore).

### Changed

- **Game pages open on the game.** One of its captures (a favourite if there
  is one near the top, otherwise the latest) runs edge to edge behind the
  title, with the capture count and first and last capture dates set large.
  Buttons and highlights on the page take a colour from the game's cover art.
- **The Library starts with "Jump back in"**: the three games you captured
  most recently, each behind its latest capture. Covers below it lose their
  card frames; hovering one outlines it in that game's colour.
- **Screenshot grids are tighter and plainer.** No border, padded footer or
  repeated date under every thumbnail, and 4 px gaps instead of 12. The date is
  in the tooltip and the viewer. Applies to game pages, the Timeline and the
  public gallery.
- **The sidebar is regrouped.** Library, Timeline and Search on top, then your
  five most recently captured games, then Upload, the importers, Duplicates
  and Settings under Tools.
- New palette (neutral near-black instead of blue-grey), and the Barlow and
  Barlow Condensed typefaces, bundled with the app so nothing is fetched from
  a font service.

### Performance

- Thumbnail badges no longer use `backdrop-filter: blur`, and cards no longer
  scale and cast a glow on hover. With hundreds of thumbnails on screen that
  was per-card compositing work for no visible gain.
- Cover colours are worked out on the server, once, when a cover is saved
  (existing covers after the first start on this version) and stored on the
  game. The browser never decodes a cover to theme a page.

### Documentation

- README, architecture notes and the Unraid template brought up to date with
  1.2.3: every environment variable is listed (the template gains import
  concurrency, upload batch limits, preview width, Special K folders, CORS
  origins and Force Ownership Fix), an Updating section explains backups
  before one-way migrations, and GameVault Sync downloads point at releases.
  Added the MIT `LICENSE` file the README already referred to.

## [1.2.3] - 2026-11-23

Steam imports are now dated by when a screenshot was taken. Includes a database
migration (`m018`): back up before updating.

### Fixed

- Steam imports used the time Steam *posted* a screenshot as the time it was
  taken. Steam posts a play session, or several days of them, as one batch a
  couple of seconds apart, so those screenshots all sat on the upload day:
  several days of play under one date, with the timeline and game page
  following the upload instead of the play. The importer now reads the real
  capture time from the file name Steam keeps for each upload
  (`YYYYMMDDHHMMSS_N.jpg`, in the local time of the PC that took it). It
  falls back to the posted time only when Steam has no such name.
- Libraries imported earlier are repaired without a click. After the update
  the server asks Steam for the capture time of every screenshot it imported
  before, in the background, for the profile of your last Steam import. Only
  file names are requested; no image is downloaded again. Screenshots Steam
  does not list publicly are repaired by running a Steam import again with
  cookies: it now corrects the date of screenshots it already has and says
  how many it corrected.
- Without a Steam API key, the importer saved Steam's 200-pixel grid
  thumbnail instead of the screenshot. Steam's current grid serves thumbnails
  as separate files, and the importer took that address for the full image.
  It now reads the screenshot's page for the real one, as it did for older
  layouts. Thumbnails imported this way are not replaced automatically:
  delete them and import again.

### Changed

- `screenshots.steam_posted_at` keeps the time Steam reported, so nothing is
  lost when `taken_at` becomes the capture time.

## [1.2.2] - 2026-11-23

Fixes for bugs found by an outside review of the code. No database migration.

### Fixed

- The public gallery never showed game covers: it only drew a cover when the
  game's cover path was present, and the public API deliberately leaves that
  path out. Public games now carry an opaque `cover_version`, which the gallery
  also adds to the cover's address so a replaced cover isn't served stale.
- Merging a game that came from a Steam import into another game dropped its
  Steam app ID, so the next Steam import created the merged-away game again.
- JPEG XR captures with very saturated colours could get white specks.
  Wide-gamut scRGB stores such colours as negative values, and the Reinhard and
  ACES curves turned large negatives into bright output. Negative values (and
  NaN) now map to black before tone mapping.
- Every JPEG XR import decoded the whole file twice, once to check it and once
  for thumbnails. The check now reads only the header; a file whose image data
  turns out to be broken is still rejected and leaves nothing behind.
- JPEG XR and 16-bit PNG decoding now honour the same 144-megapixel cap as
  every other format.
- Special K imports running in parallel could pick the same library file name
  for two different captures (same name and second, from HDR/ and SDR/ folders)
  and one would overwrite the other.
- Two identical files in one Steam or Special K import could both be imported:
  the "already importing this" claim was taken after the database check.
- Import error logs could lose messages when several screenshots failed at once.
- The import progress bar counted skipped duplicates twice and could pass 100%.
- The Steam importer sent your Steam login cookie to Steam's Web API and image
  servers as well as steamcommunity.com. It now goes to steamcommunity.com only.
  Profile names are URL-encoded.
- The public gallery's screenshot details included thumbnail paths, which
  contain the library folder name and the file's SHA-256. They are gone.
- After a container update, an open browser tab that navigated to a page it
  hadn't loaded yet showed a blank screen. It now reloads once to get the new
  version, and any other page error shows a message with a Reload button
  instead of blanking the app. Unknown addresses show "Page not found".
- When the server couldn't be reached at startup, the app opened anyway and
  every page failed. It now says so, with a Try again button.
- Changing the password no longer leaves the old sign-in in the app's memory,
  and the list of signed-in sync PCs empties right away (a password change
  signs them all out).
- The upload page waited forever if its progress stream was refused.
- Search works with filters alone (favorites, a game, a date range), and the
  search box shows the new text when you search from the top bar.
- Special K imports were labelled "Upload" in the viewer's info panel.
- GameVault Sync names non-Steam game screenshots stored under Steam's older
  64-bit folder ids, and names shortcuts since removed from Steam using
  `screenshots.vdf`.
- A failed sync upload (for example a full disk) could leave its staging folder
  behind. The login rate limiter no longer keeps every IP it has seen.

### Changed

- The container no longer re-owns every file in `/data` on each start, which
  walked the whole library and woke up Unraid's array disks. The full pass runs
  only when `/data` itself isn't owned by GameVault yet;
  `GAMEVAULT_FORCE_CHOWN=true` forces it.
- The image's Unraid template label points at the template in this repository.

## [1.2.1] - 2026-11-09

A cleanup release from a full read-through of the project. No database
migration.

### Fixed

- With a password set, JPEG XR and TIFF screenshots stayed on the 1920px
  preview: the full-size view added in 1.2.0 (`/api/screenshots/{id}/full`) was
  missing from the list of image routes that accept the sign-in token in the
  address, so the viewer's request was refused and it fell back without saying
  so. Servers with `GAMEVAULT_DISABLE_AUTH=true` were not affected.
- Renaming a game (which GameVault does by itself when a Steam placeholder like
  "App 367520" gets its real name) left search matching the old name and missing
  the new one. The rename now updates the search index in the same step.
- Renaming a game to a name another game already has returned a server error;
  it now answers `409`. When a Steam placeholder's real name is already taken
  (usually by a Special K import of the same game) the placeholder keeps its
  name, instead of GameVault Sync's request for that game failing every time.
  An empty name is rejected instead of saved.
- Deleting a game left its screenshots' entries in the search index.
- Opening a share link counted two views, one for the page and one for its
  image. The page counts one.
- The share page's "Powered by GameVault" link pointed at a GitHub account that
  isn't this project, and its link-preview tags could carry the size "None".
- Dropping `.jxr` files on the upload page did nothing and the file picker hid
  them, although the page lists JXR and the server accepts it. GIF likewise.
- "Upload" on a game's page now opens the upload page with that game selected.
- A Steam or Special K import that failed or was cancelled showed "0 imported,
  0 skipped, 0 failed" on its summary whatever had been imported. An import
  that couldn't start now says why on the selection step instead of nothing.
- Adding a game with a name that is taken shows the error in the dialog instead
  of replacing the library with an error page. Failed actions on the library and
  game pages no longer show a green check mark.
- PNG and WebP covers were sent labelled as JPEG.
- `/api/timeline` and `/api/search` accepted zero and negative `page` and
  `limit` values; `/api/timeline` had no upper limit.
- The import count in Settings ignored Special K imports.
- Keyboard shortcuts in the public gallery viewer no longer fire together with
  Ctrl, Alt or Cmd (Ctrl+H cycled the file type under the browser's History).
- The Unraid template set the per-file upload limit to 50 MB, below the server's
  own default of 512 MB and below many HDR captures. **If you installed from
  the template, change "Max Upload Size (MB)" on your container by hand**; a
  template update doesn't touch an existing container. The template's icon
  was the Vite logo as an SVG and is now a GameVault PNG.

### Added

- A **Sort** menu on the library (name, latest screenshot, most screenshots).
  The user guide described it and the page already remembered the choice, but
  nothing on screen set it.
- A browser tab title ("GameVault" instead of "frontend") and icon.
- Full-size renders have their own rows under **Settings > Performance**.

### Changed

- The game delete dialog says what it does: records go, image files stay on disk.
- Settings notes that tone-map changes apply to future imports only.
- The frontend builds with Node 22 (Node 20 is past end of life).
- CI runs the two older standalone test scripts (118 checks) that pytest skips.
- The README, user guide, architecture notes and API reference were corrected
  where they described controls that don't exist (game rename, screenshot
  delete, timeline date range, share expiry), named React 18 or stale
  counts, or missed `POST /api/games/{id}/merge`.

### Removed

- Unused code: the Jinja2 and piexif dependencies and two never-rendered HTML
  templates, four image helpers, two model modules, a frontend store and
  validators module, and stale constants.


## [1.2.0] - 2026-10-28

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

- Copies of one capture saved by different tools are now one capture. Steam's
  JPG and Special K's JXR (and PNG) of the same moment used to be listed
  separately, doubling or tripling a game's count: pairing needed identical file
  names, but GameVault names uploads by capture minute and the copies are written
  a second or two apart, often across a minute. Files now pair on import when
  they are different types from the same game, have the same shape, a close
  image fingerprint (within 12 of 128 bits; real JXR/JPG pairs measured 2-8)
  and capture times within five seconds. A capture keeps at most one file per
  type, and Ungroup or "Not a match" keeps files apart for good. Migration
  `m017` queues a background library scan at first start so existing captures
  pair without any action.
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
