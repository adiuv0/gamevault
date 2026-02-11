# GameVault

Self-hosted game screenshot manager. Import your Steam screenshots, upload manually, annotate, search, and share — all from a dark, gaming-themed web UI built for Unraid.

## Features

- **Steam Import** — 4-step wizard to bulk-import screenshots from your Steam Community profile with live progress tracking
- **Manual Upload** — drag-and-drop upload with automatic thumbnail generation and EXIF metadata extraction
- **Game Library** — grid and list views, cover art, per-game screenshot counts, and metadata from Steam/SteamGridDB/IGDB
- **Annotations** — rich text editor (Markdown) for adding notes to any screenshot
- **Full-Text Search** — FTS5-powered search across game names, filenames, descriptions, and annotations with BM25 ranking
- **Timeline View** — browse screenshots chronologically, grouped by day, with stats and date filters
- **Share Links** — generate public links with OpenGraph and Twitter Card meta tags for rich previews on Discord, Slack, etc.
- **Single-User Auth** — JWT-based authentication with password setup, change, and optional disable for LAN-only setups
- **Metadata Cascade** — automatic game metadata fetching from Steam Store API, SteamGridDB, and IGDB

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI, aiosqlite (SQLite + WAL + FTS5) |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS v4, Zustand |
| Image Processing | Pillow, piexif |
| Steam Scraping | httpx, BeautifulSoup4, lxml |
| Auth | bcrypt, python-jose (JWT) |
| Container | Multi-stage Docker (Node alpine + Python slim) |

## Quick Start

### Docker Compose

```yaml
services:
  gamevault:
    image: adiuv0/gamevault:latest
    container_name: gamevault
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - ./data:/data
    environment:
      - GAMEVAULT_SECRET_KEY=your-random-secret-key-here
      - GAMEVAULT_BASE_URL=http://localhost:8080
      - TZ=America/Chicago
```

```bash
docker compose up -d
```

Then open `http://localhost:8080` and set your password on first visit.

### Docker Run

```bash
docker run -d \
  --name gamevault \
  -p 8080:8080 \
  -v ./data:/data \
  -e GAMEVAULT_SECRET_KEY=your-random-secret-key-here \
  -e GAMEVAULT_BASE_URL=http://localhost:8080 \
  adiuv0/gamevault:latest
```

### Unraid

An XML template is included for Unraid's Docker UI. See [Unraid Installation](#unraid-installation) below.

## Configuration

All configuration is done through environment variables:

### Required

| Variable | Description |
|----------|-------------|
| `GAMEVAULT_SECRET_KEY` | Random string for JWT signing. Generate with: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `GAMEVAULT_BASE_URL` | Public URL for share links (e.g., `https://screenshots.yourdomain.com`) |

### Optional — API Keys

These enable richer game metadata and cover art. Add them anytime from the Settings page.

| Variable | Description | Get one at |
|----------|-------------|-----------|
| `GAMEVAULT_STEAM_API_KEY` | Steam Web API key for game metadata | [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey) |
| `GAMEVAULT_STEAMGRIDDB_API_KEY` | Community-curated cover art | [steamgriddb.com/profile/preferences/api](https://www.steamgriddb.com/profile/preferences/api) |
| `GAMEVAULT_IGDB_CLIENT_ID` | IGDB/Twitch client ID (non-Steam games) | [api-docs.igdb.com](https://api-docs.igdb.com/#getting-started) |
| `GAMEVAULT_IGDB_CLIENT_SECRET` | IGDB/Twitch client secret | Same as above |

### Optional — Tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `GAMEVAULT_DISABLE_AUTH` | `false` | Disable password auth (LAN-only setups) |
| `GAMEVAULT_IMPORT_RATE_LIMIT_MS` | `1000` | Delay between Steam requests during import |
| `GAMEVAULT_MAX_UPLOAD_SIZE_MB` | `50` | Max file size for uploads |
| `GAMEVAULT_THUMBNAIL_QUALITY` | `85` | JPEG quality for thumbnails (1-100) |
| `GAMEVAULT_TOKEN_EXPIRY_DAYS` | `30` | Login token lifetime |
| `TZ` | `UTC` | Container timezone |

## Volumes

| Container Path | Purpose |
|---------------|---------|
| `/data` | Database (`gamevault.db`), thumbnails, config |
| `/data/library` | Full-size screenshot files, organized by game |

You can mount `/data/library` separately if you want screenshots on a different drive or array:

```yaml
volumes:
  - /mnt/user/appdata/gamevault:/data
  - /mnt/user/screenshots:/data/library
```

## Unraid Installation

1. Go to **Docker** tab in the Unraid web UI
2. Click **Add Container**
3. Set **Repository** to `adiuv0/gamevault:latest`
4. Use the included `unraid-template.xml` or manually configure:
   - **Port**: 8080
   - **App Data**: `/mnt/user/appdata/gamevault` -> `/data`
   - **Library**: `/mnt/user/screenshots/gamevault` -> `/data/library`
   - **Secret Key**: your generated key
   - **Base URL**: your server address
5. Click **Apply**

All API keys and tuning parameters are available as fields in the Unraid Docker UI when using the template.

## Steam Import

The Steam Import wizard walks you through 4 steps:

1. **Profile URL** — enter your Steam Community profile URL
2. **Cookies** — paste your `steamLoginSecure` and `sessionid` cookies (needed because Steam doesn't have a public screenshots API)
3. **Game Selection** — pick which games to import
4. **Import** — watch live progress with per-game and overall progress bars

The importer handles:
- Rate limiting (configurable, default 1 req/sec)
- Duplicate detection by Steam screenshot ID and file hash
- Mature content gates
- Import cancellation
- Automatic thumbnail generation

## Data & Backups

Everything is in the `/data` volume. To back up:

```bash
# Stop the container first for a consistent backup
docker stop gamevault
cp -r ./data ./data-backup-$(date +%Y%m%d)
docker start gamevault
```

The SQLite database uses WAL mode, so it's safe to copy while running if you need hot backups — but stopping first is safer.

## Building from Source

```bash
git clone https://github.com/adiuv0/gamevault.git
cd gamevault

# Build Docker image
docker build -t gamevault .

# Or run locally for development
cd frontend && npm install && npm run dev &
cd .. && pip install -e ".[dev]" && uvicorn backend.main:app --reload
```

## Running Tests

```bash
pip install -e ".[dev]"

# Main test suite (106 tests)
python -m pytest tests/ -v

# Legacy standalone tests
python tests/test_phase4_steam_import.py   # 66 tests — Steam import pipeline
python tests/test_phase5_metadata_timeline.py  # 52 tests — metadata & timeline
```

## License

MIT
