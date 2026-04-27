# GameVault Architecture

A guided tour of how GameVault is built — for contributors, future-you,
or anyone forking the project.

For a feature-by-feature user view, see [USER_GUIDE.md](USER_GUIDE.md).
For REST endpoints, see [API_REFERENCE.md](API_REFERENCE.md). For runtime
configuration, see [CONFIGURATION.md](CONFIGURATION.md).

## Stack at a Glance

| Layer | Technology |
|---|---|
| Backend framework | Python 3.12, FastAPI |
| Database | SQLite via aiosqlite (WAL + FTS5) |
| Image processing | Pillow (SDR), imagecodecs + numpy (HDR JXR) |
| HTTP client | httpx (async) |
| HTML parsing | BeautifulSoup + lxml |
| Auth | bcrypt + python-jose (JWT) |
| Frontend | React 18 + TypeScript + Vite |
| Styling | Tailwind CSS v4 |
| State | Zustand stores + TanStack Query |
| Routing | react-router-dom |
| Container | Multi-stage Docker (node:20-alpine + python:3.12-slim) |

---

## Repository Layout

```
gamevault/
├── backend/                    # FastAPI app
│   ├── main.py                 # App factory, lifespan, router registration, SPA fallback
│   ├── auth.py                 # bcrypt hashing, JWT encoding, require_auth dependency
│   ├── config.py               # Settings (pydantic-settings) singleton
│   ├── database.py             # aiosqlite connection management
│   ├── migrations/             # Schema migrations + runner
│   ├── models/                 # Pydantic request/response models
│   ├── routers/                # FastAPI routers (one per feature)
│   ├── services/               # Domain logic — DB writes, file I/O, external APIs
│   └── templates/              # Jinja templates (currently only share page)
├── frontend/                   # React SPA
│   ├── src/
│   │   ├── App.tsx             # Routes
│   │   ├── api/                # Typed API clients (one per backend router)
│   │   ├── components/         # Reusable UI (gallery, games, layout, screenshots, shared, upload)
│   │   ├── lib/                # Constants, types, helpers
│   │   ├── pages/              # Top-level route components
│   │   └── stores/             # Zustand stores (auth, view, import progress)
│   └── vite.config.ts
├── cli/
│   ├── gamevault_sync.py       # Standalone sync tool (tk GUI + headless)
│   └── README.md
├── tests/                      # pytest suites
├── docs/                       # This documentation set
├── Dockerfile                  # Multi-stage build
├── docker-compose.yml          # Example deployment
├── entrypoint.sh               # gosu privilege drop
├── pyproject.toml              # Backend deps + ruff/pytest config
├── requirements.txt            # Mirror of pyproject deps for Docker
└── unraid-template.xml         # Unraid Docker UI template
```

---

## Backend

### Application bootstrap

[`backend/main.py`](../backend/main.py) wires everything:

1. Configure logging (INFO by default; httpx/httpcore quieted to WARNING)
2. Build the FastAPI `app` with a lifespan context manager
3. On startup: ensure data dirs exist, load/generate the JWT secret key,
   `init_db()` (which runs migrations)
4. Mount the router for each feature
5. Mount static frontend assets and an SPA fallback

The lifespan handler also closes the DB on shutdown.

### Routers ↔ Services split

Each feature has a router (HTTP layer) and a service (domain layer).
Routers parse request models, call services, and shape responses.
Services do the actual work — DB writes, filesystem I/O, external HTTP
calls.

| Feature | Router | Service(s) |
|---|---|---|
| Auth | [`routers/auth.py`](../backend/routers/auth.py) | `backend.auth` |
| Games | [`routers/games.py`](../backend/routers/games.py) | `services/game_service.py` |
| Screenshots | [`routers/screenshots.py`](../backend/routers/screenshots.py) | `services/screenshot_service.py` |
| Upload | [`routers/upload.py`](../backend/routers/upload.py) | `services/upload_service.py` |
| Steam Import | [`routers/steam_import.py`](../backend/routers/steam_import.py) | `services/steam_scraper.py`, `services/steam_import_service.py` |
| Special K Import | [`routers/specialk_import.py`](../backend/routers/specialk_import.py) | `services/specialk_import_service.py` |
| Search | [`routers/search.py`](../backend/routers/search.py) | `services/search_service.py` |
| Timeline | [`routers/timeline.py`](../backend/routers/timeline.py) | `services/timeline_service.py` |
| Metadata | [`routers/metadata.py`](../backend/routers/metadata.py) | `services/metadata_service.py` |
| Settings | [`routers/settings.py`](../backend/routers/settings.py) | (inline) |
| Sharing | [`routers/share.py`](../backend/routers/share.py) | `services/share_service.py` |
| Public Gallery | [`routers/gallery.py`](../backend/routers/gallery.py) | reuses `game_service` + `screenshot_service` |

Cross-cutting helpers:

- [`services/filesystem.py`](../backend/services/filesystem.py) — sanitization, directory layout, library size
- [`services/image_processor.py`](../backend/services/image_processor.py) — Pillow-based hash, EXIF, dimensions, thumbnail generation. Routes HDR sources through `hdr_processor`.
- [`services/hdr_processor.py`](../backend/services/hdr_processor.py) — JXR magic detection + decode (lazy `imagecodecs`), 16-bit PNG decode, Reinhard/ACES/clip tone-map, sRGB OETF.

### Authentication flow

`require_auth` is a FastAPI dependency added to the protected routers via
`Depends(require_auth)`. It:

1. Lets through public paths (`/api/auth/*`, `/api/health`,
   `/api/gallery/*`, `/share/*`)
2. Returns immediately if `GAMEVAULT_DISABLE_AUTH=true`
3. Checks for password setup state — sends 403 `setup_required` if no
   password is set yet (except for the `/api/auth/setup` endpoint itself)
4. Reads the JWT from `Authorization: Bearer <token>` or `?token=<jwt>`
   query param (the latter for SSE)
5. Decodes and validates the JWT signature + expiration

The query-string fallback is needed because `EventSource` can't set
custom headers.

Login rate limiting (5 fails / 15 min / IP) lives inside
`routers/auth.py` as a simple in-memory dict — not distributed, but
sufficient for single-instance deployments.

### Database

A single global aiosqlite connection (`backend/database.py:_db`). The
connection is opened at startup, runs migrations, then is reused for the
process lifetime. WAL mode enables concurrent readers; the busy timeout
is 5 seconds.

**Why one connection?** SQLite serializes writes anyway, and aiosqlite
serializes calls per-connection. A connection pool would only help reads,
which are rarely the bottleneck here. Simplicity wins.

**FTS5.** The schema includes:

- `screenshots_fts_content` — a regular table with mirrored searchable
  columns (rowid = screenshot id)
- `screenshots_fts` — an FTS5 virtual table with `content=screenshots_fts_content`
- Triggers on `_content` insert/delete/update keep the FTS index in sync

`_sync_fts` in `screenshot_service.py` upserts into `_content`; the
triggers do the rest. Search queries use the FTS5 virtual table directly
with BM25 ranking.

**Migrations.** [`migrations/runner.py`](../backend/migrations/runner.py)
maintains an `_migrations` table that tracks which files have been
applied. Each migration is a Python module with an `async upgrade(db)`
function. Migrations run sequentially during `init_db`.

### Image pipeline

The big-picture flow for any incoming image:

```
Source (upload / Steam / Special K)
        │
        ▼
[validate_image]  ← magic-byte check; for JXR uses imagecodecs decode
        │
        ▼
[get_image_format] [get_image_dimensions] [compute_sha256] [extract_exif]
        │
        ▼
   check_duplicate_hash  ← skip if already stored
        │
        ▼
   Save original to library/<game>/screenshots/<filename>
        │
        ▼
[generate_thumbnails]
        │
        ├─ if HDR source:
        │     hdr_processor.render_sdr_pil()
        │       → decode_jxr / decode_hdr_png
        │       → tone_map (algorithm + exposure from app_settings)
        │       → to_8bit_srgb
        │     → resize → JPEG → save to thumbnails/300/ and /800/
        │
        └─ else: Pillow open → convert RGB → resize → JPEG → save
        │
        ▼
   create_screenshot (DB record + FTS sync + game stats update)
```

The `image_processor` module is synchronous because Pillow is. The async
import services call into it via `asyncio.to_thread` for off-loop
execution.

#### HDR specifics

- **Detection** — JXR by magic bytes (`II\xbc`); HDR PNG by Pillow mode
  (`I;16*`) or 16-bit channel data.
- **Decode** — `imagecodecs.jpegxr_decode` returns a numpy array in the
  source's native dtype; `decode_jxr` normalizes to float32.
- **Tone map** — Reinhard / ACES / hard clip (see
  [CONFIGURATION.md](CONFIGURATION.md#algorithm-reference)). The
  algorithm and exposure are read from `app_settings` synchronously by
  `_get_tone_map_settings` opening a fresh sqlite connection.
- **Encode** — sRGB OETF then `astype(uint8)`.
- **Original preservation** — the JXR/PNG file is copied untouched to the
  library; only the thumbnails are SDR.

#### Why imagecodecs is lazy-imported

`imagecodecs` is large (~150 MB on Linux x86_64). It's only needed for
JXR decode, so `_get_imagecodecs()` imports it on first JXR encounter.
Tests that don't decode JXR don't pay the import cost.

### Steam scraping

[`steam_scraper.py`](../backend/services/steam_scraper.py) is a hybrid:

- **Game discovery** — HTML scrape of the Steam Community screenshot
  grid page. The game filter dropdown
  (`#sharedfiles_filterselect_app_filterable`) reveals every game with
  screenshots. With cookies, private screenshots' games are also visible.
- **Per-game screenshots** — Steam Web API
  (`IPublishedFileService/GetUserFiles`) when an API key is available;
  HTML scraping of the grid page as fallback.
- **Critical detail** — query `filetype=4` (the
  `EPublishedFileInfoMatchingFileType` enum) returns `file_url` populated
  with the full-res CDN link. Using `filetype=5` (the response-only
  `EWorkshopFileType` enum) returns results but with empty `file_url`.

Per-screenshot metadata (description, taken date) is parsed from
detail-page HTML when not available via API.

### SSE progress

Three import/upload flows stream progress to the client. The pattern is
shared:

1. Service holds an in-memory `dict[int, asyncio.Queue]` keyed by session ID
2. The router's progress endpoint reads from the queue and writes
   `text/event-stream`
3. `done` is the terminal event — when the client sees it, it closes
   EventSource
4. Cancellation is a separate `dict[int, bool]` flag the service polls
   between batches

This is intentionally simple — no Redis, no broker. Per-process state is
fine for a single-user self-hosted app. If you ever scale to multiple
backend instances you'd need to externalize this.

### Special K import

[`specialk_import_service.py`](../backend/services/specialk_import_service.py)
is a filesystem-driven mirror of the Steam importer. Key differences:

- No HTTP client — all I/O is local disk
- Game discovery walks the top-level subdirectories of the configured
  root path
- HDR/SDR detection by extension (`.jxr` always HDR) and folder hint
  (`HDR/` / `SDR/` / `LDR/` / `JXR/` substrings in any path component)
- Game name cleanup heuristic in `_clean_game_name` — strip `.exe`,
  split CamelCase + letter→digit boundaries

The same screenshot pipeline is reused, so HDR thumbnails come for free.

### Metadata cascade

[`metadata_service.py`](../backend/services/metadata_service.py) tries
each provider in order:

1. **Steam Store API** — no key required. Fetches name, description,
   developer, publisher, release date, genres, header image. Only when
   the game has a `steam_app_id`.
2. **SteamGridDB** — requires `steamgriddb_api_key`. Fetches a high-quality
   600×900 cover (preferred over Steam's header image).
3. **IGDB** — requires `igdb_client_id` + `igdb_client_secret`. Fallback
   for non-Steam games or anything Steam doesn't have.

The result is merged onto the game record with a "don't overwrite
existing fields" policy — so manually edited metadata isn't clobbered by
later refreshes.

`fetch_and_apply_metadata` is called after every game is created during
import. `POST /api/metadata/fetch-all` walks every game looking for
incomplete records and fills them in (with internal rate-limiting to be
polite to upstreams).

### Sharing

Tokens are 16-byte URL-safe random strings (`secrets.token_urlsafe(16)`).
The share page (`/share/{token}`) is served by the backend — not the SPA
— so OpenGraph crawlers like Discordbot don't have to execute JavaScript
to see the meta tags.

The share page is a self-contained HTML document built from a
multi-line f-string template in
[`routers/share.py`](../backend/routers/share.py). Inline styles, no
JavaScript, no external assets except the screenshot itself.

---

## Frontend

### Routing

[`App.tsx`](../frontend/src/App.tsx) sets up two route trees:

- `/gallery/*` — public gallery, wrapped in `GalleryShell`, no auth
- everything else — wrapped in `AuthGate` then `AppShell` (sidebar + top bar)

The auth gate redirects to `/login` if there's no token, or to `/setup`
if no password has been created.

### State management

Three Zustand stores in [`stores/`](../frontend/src/stores):

- `authStore` — current JWT, login state. Token mirrors `localStorage`.
- `viewStore` — UI preferences (grid vs list, sort order). Mirrors
  `localStorage`.
- `importStore` — global import progress (used by the top bar to show a
  small badge during long imports).

Server data is mostly fetched ad-hoc with `useEffect` + `useState`;
TanStack Query is set up but used only by parts of the gallery flow that
need cache + revalidation. Adding TanStack Query elsewhere is a
non-breaking change if you want it.

### API clients

One TypeScript module in [`api/`](../frontend/src/api/) per backend
router. Each exports typed functions wrapping fetch + `Authorization`
header. The shared `client.ts` provides:

- `api.get` / `post` / `put` / `delete` — typed JSON requests
- `api.uploadFiles` — multipart/form-data with progress callbacks (for
  the upload page)
- `api.connectSSE` — generic EventSource helper
- `tokenQs()` — returns `?token=<jwt>` for SSE/img URLs

### Pages

| Route | Component | Purpose |
|---|---|---|
| `/` | [`LibraryPage`](../frontend/src/pages/LibraryPage.tsx) | Game grid/list, "Add Game", "Fix Metadata", "Clean Up Empty" |
| `/games/:id` | [`GamePage`](../frontend/src/pages/GamePage.tsx) | One game's screenshots + metadata |
| `/timeline` | [`TimelinePage`](../frontend/src/pages/TimelinePage.tsx) | Day-grouped chronological view |
| `/upload` | [`UploadPage`](../frontend/src/pages/UploadPage.tsx) | Drag-and-drop with SSE progress |
| `/import/steam` | [`SteamImportPage`](../frontend/src/pages/SteamImportPage.tsx) | 4-step wizard |
| `/import/specialk` | [`SpecialKImportPage`](../frontend/src/pages/SpecialKImportPage.tsx) | 3-step filesystem importer |
| `/search` | [`SearchPage`](../frontend/src/pages/SearchPage.tsx) | FTS5 search + filters |
| `/settings` | [`SettingsPage`](../frontend/src/pages/SettingsPage.tsx) | All runtime config |
| `/login` / `/setup` | [`LoginPage`](../frontend/src/pages/LoginPage.tsx) | Combined screen |
| `/gallery/...` | [`pages/gallery/*`](../frontend/src/pages/gallery/) | Public gallery |

### Theme

Tailwind v4 is configured in [`index.css`](../frontend/src/index.css)
with CSS custom properties for the dark theme. Color tokens follow a
semantic naming pattern (`bg-bg-primary`, `text-text-secondary`,
`border-border`, `accent-primary` / `success` / `warning` / `danger`).
There's no light theme.

---

## CLI: GameVault Sync

[`cli/gamevault_sync.py`](../cli/gamevault_sync.py) is a single-file
Python script that runs on a user's gaming PC. It supports two scan
sources controlled by `--mode {steam,specialk,both}`:

**Steam mode** (default for backward compatibility):

1. Auto-detect Steam install via common paths
2. Walk `<Steam>/userdata/<user>/760/remote/<appid>/screenshots/`
3. Cross-reference with `screenshots.vdf` for original dimensions / time

**Special K mode**:

1. Walk a user-supplied root path
2. Treat each top-level subdirectory as a game
3. Collect `.jxr` and `.png` files recursively under each
4. Clean folder names client-side (mirrors `_clean_game_name` from the
   server's [specialk_import_service](../backend/services/specialk_import_service.py))
   so the resulting display name converges with what the server's
   filesystem importer would produce

After scanning (one or both sources), the unified flow is:

1. Hash every discovered file with SHA-256
2. Bulk-call `POST /api/screenshots/check-hashes` to find which files
   the server doesn't have yet
3. Resolve the GameVault game record per source:
   - Steam: `GET /api/games/by-steam-appid/{appid}`
   - Special K: `POST /api/games/by-name` with the cleaned folder name
4. Upload new files via `POST /api/upload/sync`

The two sources merge naturally — a Special K folder named the same as a
Steam-imported game lands on the same `games` row, so screenshots from
both sources show up under one library entry.

Two execution modes: tkinter GUI (default) and headless CLI
(`--no-gui`). Settings are saved to `~/.gamevault_sync.json`, including
the chosen mode and both paths.

The CLI's only third-party dependency is httpx — VDF parsing is
embedded.

### Why two Special K paths?

There are two distinct ways to import Special K screenshots:

| Path | Reads from | Use when |
|---|---|---|
| **Web UI: Special K Import page** | Server-side filesystem | GameVault and Special K both run on the same machine, or you have a bind-mount |
| **Sync CLI: Special K mode** | Local-PC filesystem | GameVault runs remotely (e.g. Unraid) and can't see your gaming PC's drives |

They produce identical library entries (same hash dedup, same metadata
shape). Pick whichever matches your deployment topology.

---

## Testing

Tests live in [`tests/`](../tests). Run with:

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
```

[`conftest.py`](../tests/conftest.py) overrides env vars before any
backend module imports, redirecting paths to a `tempfile.mkdtemp()`
directory and disabling auth so tests can hit endpoints freely.

Test files:

- [`test_core_services.py`](../tests/test_core_services.py) — DB init,
  schema, FTS, filesystem, image processing, auth, search, timeline
- [`test_steam_parsers.py`](../tests/test_steam_parsers.py) — Steam HTML
  parsing + URL building (uses HTML fixtures)
- [`test_phase4_steam_import.py`](../tests/test_phase4_steam_import.py),
  [`test_phase5_metadata_timeline.py`](../tests/test_phase5_metadata_timeline.py)
  — legacy standalone runners (excluded from pytest collection — run
  directly with `python tests/test_phaseN_*.py`)
- [`test_phase6_share_settings.py`](../tests/test_phase6_share_settings.py)
  — share + settings integration
- [`test_specialk_hdr.py`](../tests/test_specialk_hdr.py) — HDR
  detection, tone-map algorithms, sRGB encoding, name cleaning, scanner
  behaviour

Total: **135 pytest-collected tests** plus the legacy phase4/5 standalone
suites (118 tests combined).

---

## Docker Build

Multi-stage:

1. **`frontend-builder`** — `node:20-alpine`, runs `npm ci` then `npm run
   build`, leaves the built SPA in `frontend/dist/`.
2. **Production image** — `python:3.12-slim`, installs system libs
   (`libjpeg62-turbo`, `libpng-dev`, `libwebp-dev`, `libxml2-dev`,
   `libxslt1-dev`, `gosu`), pip-installs from `requirements.txt`, copies
   the backend code and the built frontend, creates a non-root
   `gamevault` user.

`entrypoint.sh` runs as root, `chown -R`s `/data` to the gamevault user
(needed for mounted volumes that come in owned by some other UID), then
`exec gosu gamevault "$@"`.

The `imagecodecs` wheel (~50–150 MB) is the heaviest dependency — bundles
its own native libs so no apt packages are needed for it. Rest of Pillow
needs the libjpeg/libpng/libwebp packages.

---

## Hot Spots — where to start reading

If you're new to the code, this is the order I'd recommend:

1. [`backend/main.py`](../backend/main.py) — see how the app is composed
2. [`backend/services/screenshot_service.py`](../backend/services/screenshot_service.py)
   — central CRUD with FTS sync
3. [`backend/services/image_processor.py`](../backend/services/image_processor.py)
   + [`hdr_processor.py`](../backend/services/hdr_processor.py) — the
   pipeline most data flows through
4. [`backend/services/specialk_import_service.py`](../backend/services/specialk_import_service.py)
   or [`steam_import_service.py`](../backend/services/steam_import_service.py)
   — how SSE-progressed bulk operations are structured
5. [`frontend/src/pages/SpecialKImportPage.tsx`](../frontend/src/pages/SpecialKImportPage.tsx)
   — how the frontend consumes SSE
6. [`frontend/src/pages/SettingsPage.tsx`](../frontend/src/pages/SettingsPage.tsx)
   — patterns for runtime preferences

---

## Known Constraints + Future Work

- **Single-user.** All endpoints assume one logical user. Adding
  multi-user would require schema changes (per-screenshot ownership) and
  auth changes (separate sessions, role checks).
- **Single-instance.** Progress queues + cancel flags + login rate
  limiting are in-process. Scaling beyond one process means
  externalizing all three (Redis pub/sub for SSE, persistent storage for
  rate limiting).
- **No bulk thumbnail regeneration.** Changing tone-map settings doesn't
  retroactively update existing thumbnails. Adding an endpoint to walk
  all screenshots and re-call `generate_thumbnails` would be
  straightforward.
- **No per-screenshot tags.** The schema has a `tags` and
  `screenshot_tags` table but they're not exposed in the UI yet.
- **JXR EXIF.** `imagecodecs` doesn't expose JXR IFD metadata, so JXR
  uploads have no EXIF data and `taken_at` falls back to file mtime.
