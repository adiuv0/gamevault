# GameVault Configuration Reference

Two layers of configuration:

1. **Environment variables** (`GAMEVAULT_*`) — set at container start, used
   for paths, secrets, and tuning.
2. **`app_settings` DB table** — runtime preferences set via the web UI
   (API keys, Special K path, tone-map settings, password hash).

DB values **override** env vars when both are set for the same key.

For installation see the main [README.md](../README.md). For per-feature
behavior see [USER_GUIDE.md](USER_GUIDE.md).

---

## Environment Variables

All env vars use the `GAMEVAULT_` prefix and are loaded by `pydantic-settings`
in [backend/config.py](../backend/config.py). They can also be set via a
`.env` file in the project root.

### Required (effectively)

| Variable | Default | Notes |
|---|---|---|
| `GAMEVAULT_SECRET_KEY` | auto-generated on first run | JWT signing key. If left default, GameVault generates a random 32-byte hex key on first boot and persists it to `/data/.secret_key` so tokens survive restarts. Set this explicitly to a long random value (e.g. `python -c "import secrets; print(secrets.token_hex(32))"`) for production deployments. |
| `GAMEVAULT_BASE_URL` | `http://localhost:8080` | Public URL for share links. Set to your real public URL or share previews on Discord/Slack/etc. won't work. |

### Paths

| Variable | Default | Notes |
|---|---|---|
| `GAMEVAULT_DATA_DIR` | `/data` | Holds `gamevault.db`, `.secret_key`, thumbnails directory, library. |
| `GAMEVAULT_LIBRARY_DIR` | `/data/library` | Per-game folders with `screenshots/`, `thumbnails/300/`, `thumbnails/800/`, `metadata/`. Mount on a separate drive if you want screenshots elsewhere. |
| `GAMEVAULT_DB_PATH` | `/data/gamevault.db` | SQLite database file (WAL mode). |

### Authentication

| Variable | Default | Notes |
|---|---|---|
| `GAMEVAULT_DISABLE_AUTH` | `false` | If `true`, all auth checks are skipped. Only safe behind a trusted reverse proxy that does its own auth. |
| `GAMEVAULT_TOKEN_EXPIRY_DAYS` | `30` | JWT lifetime. Tokens are not revocable, so shorter is more secure if you don't have separate session management. |

### Optional API Keys

These can also be set in the UI (Settings → API Keys). DB-stored values
take precedence over env vars.

| Variable | Purpose |
|---|---|
| `GAMEVAULT_STEAM_API_KEY` | Steam Web API key. Faster screenshot discovery + richer Steam Store metadata. |
| `GAMEVAULT_STEAMGRIDDB_API_KEY` | High-quality cover art from SteamGridDB. |
| `GAMEVAULT_IGDB_CLIENT_ID` | Twitch/IGDB client ID for non-Steam metadata. |
| `GAMEVAULT_IGDB_CLIENT_SECRET` | Twitch/IGDB client secret. |

### Tuning

| Variable | Default | Notes |
|---|---|---|
| `GAMEVAULT_IMPORT_RATE_LIMIT_MS` | `1000` | Delay between Steam HTML requests during import. Lower = faster but may hit Steam's rate limiter. |
| `GAMEVAULT_MAX_UPLOAD_SIZE_MB` | `50` | Per-file upload limit. Web requests over this return HTTP 413. |
| `GAMEVAULT_THUMBNAIL_QUALITY` | `85` | JPEG quality (1-100) for generated thumbnails. |
| `GAMEVAULT_CORS_ORIGINS` | `""` | Comma-separated list of additional origins beyond the localhost defaults. Required if you serve the frontend from a different host than the backend. |
| `GAMEVAULT_SPECIALK_ALLOWED_ROOTS` | `""` | Comma-separated absolute paths the Special K importer is allowed to scan. **Required when `GAMEVAULT_DISABLE_AUTH=true`** — otherwise the endpoint refuses with HTTP 403. When auth is enabled, an empty value falls back to "any path" for backward compatibility. See [Special K import safety](#special-k-import-safety) below. |
| `TZ` | `UTC` | Container timezone. Affects timestamps in logs and the `taken_at` displayed in the UI. |

### Auto-managed

These are set automatically and rarely need overriding:

- `GAMEVAULT_DATA_DIR`, `GAMEVAULT_LIBRARY_DIR`, `GAMEVAULT_DB_PATH` are
  baked into the Docker image.

---

## DB-stored Preferences (`app_settings` table)

Schema: `(key TEXT PRIMARY KEY, value TEXT NOT NULL)`. Managed via the
Settings page and the `PUT /api/settings/preferences` /
`PUT /api/settings/api-keys` endpoints — don't write directly.

### Preferences (set via `PUT /api/settings/preferences`)

| Key | Type | Range / Values | Default | Notes |
|---|---|---|---|---|
| `specialk_path` | string | any path | `""` | Pre-fills the Special K Import page. Server-side path. |
| `tone_map_algorithm` | enum | `reinhard` \| `aces` \| `clip` | `reinhard` | Used when generating SDR thumbnails for HDR sources. |
| `tone_map_exposure` | float | `0.05` – `8.0` | `1.0` | Multiplier applied before tone-mapping. |

Tone-map settings are read by [`image_processor.py`](../backend/services/image_processor.py)
on each thumbnail generation, opening a fresh sqlite connection
(synchronous) since the surrounding Pillow code is synchronous. Changes
take effect on the next thumbnail generated; existing thumbnails are not
regenerated.

### API Keys (set via `PUT /api/settings/api-keys`)

| Key | Notes |
|---|---|
| `steam_api_key` | Plain-text storage. |
| `steamgriddb_api_key` | "" |
| `igdb_client_id` | "" |
| `igdb_client_secret` | "" |

The `get_effective_key(name)` helper reads the DB first, then falls back
to the matching env var on the `Settings` Pydantic object. Used wherever
metadata or scraping code needs an API key.

### Auth-managed

- `auth_password_hash` — bcrypt hash of the user's password. Set by
  `POST /api/auth/setup` and `POST /api/auth/change-password`. Cleared
  only by direct DB manipulation.

---

## Tone Mapping in Practice

Setting `tone_map_algorithm` and `tone_map_exposure` only affects future
thumbnail generation. To regenerate existing thumbnails after changing
these settings, you currently need to:

1. Note the IDs of the screenshots you want to regenerate
2. Delete the entries (`DELETE /api/screenshots/{id}` removes files)
3. Re-import or re-upload the originals

A bulk-regeneration endpoint is not implemented (yet). If you want one,
file an issue.

### Algorithm reference

| Algorithm | Formula | Behavior |
|---|---|---|
| `reinhard` | `x / (1 + x)` | Soft rolloff. `0 → 0`, `1 → 0.5`, `∞ → 1`. The default. |
| `aces` | Narkowicz approximation | Filmic curve, slightly more contrast and saturation. |
| `clip` | `min(x, 1.0)` | Hard clip. Fastest, blows highlights. |

After tone-mapping, the linear-ish output is encoded with the sRGB OETF
(industry-standard for SDR displays).

---

## Library Layout on Disk

```
{GAMEVAULT_DATA_DIR}/                                # default /data
├── gamevault.db                                     # SQLite (WAL)
├── gamevault.db-wal                                 # WAL log
├── gamevault.db-shm                                 # WAL shared memory
├── .secret_key                                      # auto-generated JWT key
├── library/                                         # GAMEVAULT_LIBRARY_DIR
│   ├── Cyberpunk 2077/
│   │   ├── screenshots/
│   │   │   ├── Cyberpunk 2077 2025_01_03 14_22.jpg
│   │   │   ├── steam_3041234567890123.jpg           # from Steam import
│   │   │   ├── specialk_20251204_201822_001.jxr    # from Special K import
│   │   │   └── specialk_20251204_201833_002.png
│   │   ├── thumbnails/
│   │   │   ├── 300/                                 # grid view (300px wide)
│   │   │   └── 800/                                 # gallery preview (800px)
│   │   ├── metadata/                                # reserved, currently unused
│   │   └── cover.jpg                                # auto-fetched cover
│   └── Elden Ring/
│       └── ...
└── ...
```

The `screenshots/` directory holds the **original** files at their native
format and resolution. The `thumbnails/` directory holds the SDR JPEG
derivatives shown in the UI. HDR JXR/PNG screenshots are downloaded from
the gallery as the original file via `GET /api/screenshots/{id}/image`.

---

## Backups

Everything that matters lives under `GAMEVAULT_DATA_DIR`. To back up:

```bash
docker stop gamevault
cp -r /mnt/user/appdata/gamevault /mnt/user/backups/gamevault-$(date +%Y%m%d)
docker start gamevault
```

WAL mode means hot copies are usually safe, but stopping is safer. The
database file (`gamevault.db` plus `-wal` and `-shm` siblings during
operation), all images, and the secret key are all in `DATA_DIR`.

To restore: stop GameVault, copy the backup tree to `GAMEVAULT_DATA_DIR`,
start GameVault. No migrations or extra steps.

---

## Migrations

Schema changes are applied automatically on startup by
[backend/migrations/runner.py](../backend/migrations/runner.py). Migrations
are listed in order in `MIGRATIONS`; an `_migrations` table tracks which
have been applied so they only run once.

Current migrations:

- `m001_initial` — all core tables, indexes, FTS5 virtual table, triggers
- `m002_gallery` — adds `is_public` column to `games`
- `m003_specialk` — adds `specialk_import_sessions` table

To add a new migration: create `mNNN_<name>.py` with an
`async upgrade(db)` function and append it to `MIGRATIONS` in
`runner.py`. Migrations run inside a single connection — if `upgrade`
fails the migration record won't be inserted, so it'll be retried next
boot.

---

## Reverse Proxy Notes

GameVault is a single FastAPI app on port 8080 (configurable via
uvicorn's `--port`). All endpoints are under `/api`, `/share`,
`/assets`, or static SPA files. Standard reverse-proxy config works:

- WebSockets are not used; SSE is over plain HTTP
- For SSE to work through nginx, set `proxy_buffering off;` (the
  `X-Accel-Buffering: no` response header tries to do this automatically)
- If proxying to a different hostname, set `GAMEVAULT_BASE_URL` to your
  public URL
- Set `GAMEVAULT_CORS_ORIGINS` if your frontend is served from a
  different origin

### Login rate limiting behind a proxy

Login (`POST /api/auth/login`) and password change (`POST /api/auth/change-password`)
are independently rate-limited at 5 failed attempts per IP per 15-minute
window. The per-IP key is read from `request.client.host`, which behind a
proxy will be the proxy's address — not the real client IP. Two options:

1. **Let the proxy do the rate limiting.** Most reverse proxies have
   built-in support and can key on `X-Forwarded-For`:
   - **nginx**: `limit_req_zone $binary_remote_addr zone=auth:10m rate=10r/m;`
     applied to `/api/auth/`
   - **Traefik**: a `RateLimit` middleware on the auth router
   - **Caddy**: the `rate_limit` plugin
   This is the recommended approach for any internet-exposed deployment.

2. **Trust the proxy via Uvicorn's `--proxy-headers`.** Run uvicorn with
   `--proxy-headers --forwarded-allow-ips=<your-proxy-ip>`. GameVault
   will then see real client IPs in `request.client.host` and the
   built-in limiter will work as intended. Don't enable this unless you
   actually have a trusted proxy in front — without one, attackers can
   spoof `X-Forwarded-For` and bypass rate limiting entirely.

The in-process limiter is sufficient for single-instance LAN setups; for
public deployments lean on the proxy.

## Entrypoint behavior

[`entrypoint.sh`](../entrypoint.sh) runs as root on container start, then
`chown -R gamevault:gamevault /data` before dropping to the `gamevault`
user via `gosu`. This is what makes mounted volumes (which arrive owned
by the host user, often UID 99 on Unraid) writable inside the container.

**Side effects:**

- Slow on startup for large libraries (every file is touched). Containers
  with millions of screenshots may take tens of seconds to start.
- The chown is recursive — bind-mounting unrelated directories under
  `/data` will rewrite their ownership. Mount only what GameVault needs.
- If you're certain your volume's ownership already matches, you can
  skip the chown by overriding the entrypoint or running with
  `--user gamevault:gamevault` directly.

---

## Special K import safety

The [Special K Import page](USER_GUIDE.md#special-k-import-hdr--sdr) reads
files from a path the GameVault server can see. Because the path is
caller-supplied, an attacker with API access (or anyone, if auth is off)
could otherwise enumerate and copy any image-shaped file the server can
read. Two protections gate this:

| Mode | `GAMEVAULT_SPECIALK_ALLOWED_ROOTS` | Auth | Behavior |
|---|---|---|---|
| 1 | set | enabled | Scan path must resolve under one of the configured roots — strictest. Recommended for public-facing deployments. |
| 2 | empty | enabled | Any path the server can read. Backward compatible — the JWT is the authorization signal. |
| 3 | empty | **disabled** | Endpoint refuses with HTTP 403. Without an allowlist or auth, the feature would be a public file-disclosure primitive. Operator must explicitly opt in by setting the allowlist. |

To enable Mode 1, set the env var to a comma-separated list of absolute
directories:

```env
GAMEVAULT_SPECIALK_ALLOWED_ROOTS=/data/specialk,/mnt/screenshots
```

In Docker, these must be **container** paths — bind-mount your host
Special K folders into the container first, then list the in-container
mount points here.

## Sample `.env`

```env
# Required
GAMEVAULT_SECRET_KEY=replace-me-with-a-real-32-byte-hex-string
GAMEVAULT_BASE_URL=https://screenshots.example.com

# Optional API keys (or set in UI)
GAMEVAULT_STEAM_API_KEY=...
GAMEVAULT_STEAMGRIDDB_API_KEY=...
GAMEVAULT_IGDB_CLIENT_ID=...
GAMEVAULT_IGDB_CLIENT_SECRET=...

# Tuning
GAMEVAULT_MAX_UPLOAD_SIZE_MB=100
GAMEVAULT_THUMBNAIL_QUALITY=90
GAMEVAULT_TOKEN_EXPIRY_DAYS=14
TZ=America/Chicago

# Special K importer allowlist (required if GAMEVAULT_DISABLE_AUTH=true)
# GAMEVAULT_SPECIALK_ALLOWED_ROOTS=/data/specialk,/mnt/screenshots
```

For Docker Compose, see the example in the main [README.md](../README.md).
