# GameVault API Reference

Every backend route in one place. The base URL is your GameVault host plus
`/api`. All admin endpoints require a JWT in the `Authorization: Bearer
<token>` header (or `?token=<jwt>` query string for SSE). Public endpoints
are explicitly noted.

For a feature-by-feature walkthrough see [USER_GUIDE.md](USER_GUIDE.md).
For configuration see [CONFIGURATION.md](CONFIGURATION.md).

## Contents

- [Authentication](#authentication)
- [Health](#health)
- [Games](#games)
- [Screenshots](#screenshots)
- [Upload](#upload)
- [Steam Import](#steam-import)
- [Special K Import](#special-k-import)
- [Search](#search)
- [Timeline](#timeline)
- [Metadata](#metadata)
- [Settings](#settings)
- [Sharing](#sharing)
- [Public Gallery](#public-gallery)
- [Server-Sent Events](#server-sent-events)

---

## Conventions

- **Auth.** All `/api/*` routes require a JWT *except* `/api/auth/*`,
  `/api/health`, `/api/gallery/*`, and `/share/*`. When
  `GAMEVAULT_DISABLE_AUTH=true` no auth is enforced.
- **Errors.** Standard FastAPI shape: `{"detail": "..."}` with the
  appropriate HTTP status code.
- **Dates.** ISO 8601 strings (UTC, no timezone suffix) unless noted.
- **Pagination.** Endpoints that paginate accept `page` (1-indexed) and
  `limit`, and return `{ "results": [...], "total": N, "page": P, "limit":
  L, "has_more": bool }`.
- **SSE.** Endpoints that stream progress speak `text/event-stream` with
  named events. EventSource cannot send custom headers, so the JWT is
  passed as a `?token=` query parameter.

---

## Authentication

### `GET /api/auth/verify` — *public*

Check current auth state. Returns `{ authenticated, setup_required,
auth_disabled }`. Use to decide whether to show the setup screen, login
screen, or the app.

### `POST /api/auth/setup` — *public, first run only*

Create the initial password. Body: `{ "password": "..." }` (min 6 chars).
Fails with 400 if a password already exists. Returns `{ token,
expires_in_days }`.

### `POST /api/auth/login` — *public*

Body: `{ "password": "..." }`. Returns `{ token, expires_in_days }` on
success. Rate-limited to 5 failures per IP per 15 minutes (returns 429
when exceeded).

### `POST /api/auth/change-password` — *auth required*

Body: `{ "current_password": "...", "new_password": "..." }`. New password
must be at least 6 chars.

---

## Health

### `GET /api/health` — *public*

Returns `{ "status": "ok", "version": "0.1.0" }`. Used by Docker
HEALTHCHECK.

### `GET /api/settings/health` — *auth required*

Same shape, but inside the auth-protected settings router.

---

## Games

### `GET /api/games?sort=<sort>`

List all games. `sort`: `name` (default), `date`, `count`. Returns
`{ "games": [...], "total": N }`.

### `POST /api/games` — *201*

Create a new game. Body: `{ "name": "...", "steam_app_id": null }`.

### `GET /api/games/{id}`

Get a single game by ID.

### `PUT /api/games/{id}`

Update game fields. All fields optional — only non-null values are
applied. Body fields: `name`, `steam_app_id`, `developer`, `publisher`,
`release_date`, `genres`, `description`, `is_public`.

### `DELETE /api/games/{id}`

Remove a game record from the database. Files on disk are **not** deleted —
that's a manual step for safety.

### `GET /api/games/{id}/cover`

Serves the game's cover image as `image/jpeg`. 404 if no cover.

### `GET /api/games/{id}/screenshots?page=1&limit=50&sort=date_desc`

Paginated list of screenshots for a game. Sort: `date_desc` (default),
`date_asc`, `name`.

### `POST /api/games/{id}/refresh-metadata`

Re-fetch metadata from Steam Store API → SteamGridDB → IGDB. Returns the
result of the metadata fetch (which sources succeeded, what fields were
updated).

### `GET /api/games/by-steam-appid/{appid}`

Get-or-create by Steam app ID. If the game doesn't exist, fetches the real
name from the Steam Store API and creates the record. If it exists with a
placeholder name (`App {id}`), tries to upgrade the name. Used by the Sync
CLI for the Steam workflow.

### `POST /api/games/by-name`

Get-or-create by display name. Body: `{ "name": "..." }`. Used by the Sync
CLI for the Special K workflow — the cleaned folder name becomes the
canonical game name, and existing games with a matching name (e.g. created
by an earlier Steam import) are returned as-is so the two sources merge
into one library entry. 400 if `name` is empty.

### `POST /api/games/cleanup-empty`

Deletes every game with zero screenshots. Returns `{ "deleted_count": N,
"deleted_games": [...] }`.

---

## Screenshots

### `GET /api/screenshots/{id}`

Returns full screenshot details + a `has_annotation` flag.

### `DELETE /api/screenshots/{id}`

Delete the screenshot record AND its files (full image + thumbnails).
Different from game deletion which leaves files alone.

### `GET /api/screenshots/{id}/image`

Serves the full-resolution image. Content-Type is detected from extension
— `.jxr` is served as `image/vnd.ms-photo` so browsers offer download
rather than trying to render. CSP headers prevent inline script execution.

### `GET /api/screenshots/{id}/thumb/{sm|md}`

Thumbnail. `sm` = 300px wide, `md` = 800px wide. Always `image/jpeg`.
Falls back to the full image if no thumbnail exists.

### `POST /api/screenshots/{id}/favorite`

Toggle favorite state. Returns `{ "is_favorite": bool }`.

### `GET /api/screenshots/{id}/annotation`

Get the annotation. 404 if none.

### `POST /api/screenshots/{id}/annotation`

Create or update the annotation. Body: `{ "content": "<markdown>" }`.
Server renders Markdown to HTML and stores both. FTS index is updated.

### `DELETE /api/screenshots/{id}/annotation`

Remove the annotation. FTS index is updated.

### `POST /api/screenshots/check-hashes`

Bulk hash existence check. Body: `{ "hashes": ["sha256...", ...] }`.
Returns `{ "existing": [...], "new": [...] }`. Used by the Sync CLI to
avoid uploading duplicates.

### `POST /api/screenshots/{id}/share`

See [Sharing](#sharing).

---

## Upload

### `POST /api/upload`

Multipart form upload. Fields:

- `files`: one or more files (repeated form field)
- `game_id`: integer, required
- `taken_at`: optional ISO 8601 override

Returns `{ "task_id": "<uuid>", "file_count": N }`. Use the task_id with
`/api/upload/progress/<task_id>` to stream per-file progress over SSE.

Rejects files larger than `GAMEVAULT_MAX_UPLOAD_SIZE_MB` with HTTP 413.

**Format whitelist** (matched against magic bytes, not extension): JPEG,
PNG, WebP, BMP, TIFF, GIF, JXR.

### `POST /api/upload/sync`

Same shape as `POST /api/upload` but processes inline and returns the
final result synchronously: `{ "uploaded": N, "screenshots": [...] }`.
Used by the Sync CLI which needs to know each upload's outcome
immediately. No SSE.

### `GET /api/upload/progress/{task_id}` — *SSE*

Stream of `{type, ...}` JSON events. Event types: `start`, `file_start`,
`file_complete`, `file_skipped`, `file_error`, `complete`, `keepalive`.

---

## Steam Import

### `GET /api/steam/api-key-status`

Returns `{ "has_api_key": bool }`. Used by the import wizard to show a
banner about API-key vs HTML-scraping mode.

### `POST /api/steam/validate`

Body: `{ "user_id": "...", "steam_login_secure": "...", "session_id":
"..." }`. Cookies are optional. Returns
`SteamValidateResponse` with `valid`, `profile_name`, `avatar_url`,
`is_numeric_id`, and `error` (only set when `valid: false`).

### `POST /api/steam/games`

Same body as `/validate`. Returns a list of `SteamGameInfo`:
`{ app_id, name, screenshot_count }`. With an API key, counts are exact.
Without one, counts come from HTML scraping and may be 0 if not visible
on the grid page.

### `POST /api/steam/import`

Start a background import. Body adds `game_ids` (empty = all) and
`is_numeric_id` to the validate body. Returns `{ "session_id": int }`.

### `GET /api/steam/import/{session_id}` — *snapshot*

Get current session status. Returns `SteamImportSessionResponse` —
includes status, total/completed games, total/completed/skipped/failed
screenshots, timestamps.

### `GET /api/steam/import/{session_id}/progress` — *SSE*

Stream of named events:

| Event | Payload |
|---|---|
| `import_started` | `{ session_id, status }` |
| `status` | `{ message }` |
| `profile_validated` | `{ profile_name, avatar_url }` |
| `games_discovered` | `{ total_games, total_screenshots, games: [...] }` |
| `game_start` | `{ app_id, name, game_index, total_games }` |
| `screenshot_complete` | `{ steam_id, filename, game_name, index, total_for_game, overall_progress, total_screenshots }` |
| `screenshot_skipped` | `{ steam_id, reason, game_name, ... }` |
| `screenshot_failed` | `{ steam_id, error, game_name }` |
| `game_complete` | `{ app_id, name, completed, skipped, failed, overall_completed, overall_skipped, overall_failed, total_screenshots }` |
| `game_error` | `{ app_id, name, error }` |
| `import_complete` | `{ session_id, completed, skipped, failed, total_games }` |
| `import_error` | `{ error }` |
| `import_cancelled` | `{ session_id }` |
| `done` | `{}` — terminal event; close the EventSource |

### `POST /api/steam/import/{session_id}/cancel`

Request cancellation. Mid-flight screenshots may still complete; new ones
won't start. Returns 400 if the session isn't running.

---

## Special K Import

Same overall shape as Steam Import, but with a filesystem scanner instead
of cookies + scraping.

### `POST /api/specialk/scan`

Body: `{ "path": "/path/to/specialk/profiles" }`. The path is resolved
**from the GameVault server's perspective** — for Docker installs, this
is the in-container path of a bind mount.

Returns `SpecialKScanResponse`:

```json
{
  "valid": true,
  "path": "...",
  "total_games": 5,
  "total_screenshots": 213,
  "games": [
    {
      "folder_name": "Cyberpunk2077",
      "suggested_name": "Cyberpunk 2077",
      "screenshot_count": 47,
      "has_hdr": true,
      "has_sdr": false
    }
  ]
}
```

If the path is invalid or empty, `valid: false` and `error` is populated.

### `POST /api/specialk/import`

Body: `{ "path": "...", "folder_names": ["GameA", "GameB"] }`. Empty
`folder_names` imports everything found. Returns `{ "session_id": int }`.

### `GET /api/specialk/import/{session_id}` — *snapshot*

Returns `SpecialKImportSessionResponse` (same shape as Steam's session
response, with `scan_path` instead of `steam_user_id`).

### `GET /api/specialk/import/{session_id}/progress` — *SSE*

Same event vocabulary as Steam Import, with the following differences:

- `game_start` and `game_complete` use `folder_name` and `name` instead
  of `app_id` and `name`
- `screenshot_*` events identify the file by `filename` (no Steam IDs)
- `screenshot_skipped` reasons: `"duplicate_hash"`
- `games_discovered.games` items include `has_hdr` and `has_sdr` booleans

### `POST /api/specialk/import/{session_id}/cancel`

Same semantics as Steam cancel.

---

## Search

### `GET /api/search?q=<query>&...`

FTS5 full-text search. Query parameters:

| Param | Type | Default | Notes |
|---|---|---|---|
| `q` | string | `""` | empty = list-all with filters |
| `game_id` | int | — | restrict to one game |
| `date_from` | ISO date | — | matches `taken_at` or `uploaded_at` |
| `date_to` | ISO date | — | "" |
| `favorites_only` | bool | `false` | "" |
| `sort` | enum | `relevance` | `relevance` \| `date_desc` \| `date_asc` |
| `page` | int | 1 | |
| `limit` | int | 50 | hard-capped at 100 |

Returns `{ results, total, page, limit, has_more }`. Each result includes
`screenshot_id`, `game_id`, `game_name`, `filename`, `file_path`,
`thumbnail_path_sm`, `thumbnail_path_md`, `taken_at`, `uploaded_at`,
`is_favorite`, `width`, `height`, `file_size`, `has_annotation`,
`annotation_preview` (~150 char), `relevance_score`.

**BM25 weights** (with non-empty query): `annotation_content`=10,
`game_name`=5, `steam_description`=3, `filename`=1.

**Tokenizer.** porter unicode61. Words are tokenized on whitespace and
hyphens. Each query word becomes a `"word"*` prefix term.

---

## Timeline

### `GET /api/timeline?...`

Screenshots grouped by day, paginated by days. Query parameters:

| Param | Default | Notes |
|---|---|---|
| `game_id` | — | filter to one game |
| `start_date` | — | `YYYY-MM-DD`, inclusive |
| `end_date` | — | `YYYY-MM-DD`, inclusive |
| `page` | 1 | |
| `limit` | 30 | days per page |

Returns:

```json
{
  "days": [
    {
      "date": "2026-04-25",
      "screenshot_count": 12,
      "games": ["Game A", "Game B"],
      "screenshots": [/* up to 50 thumbnails */]
    }
  ],
  "total_days": 47,
  "page": 1,
  "has_more": true
}
```

### `GET /api/timeline/stats`

Returns `{ total_screenshots, total_days, earliest_date, latest_date,
total_games }`.

---

## Metadata

### `POST /api/metadata/fetch/{game_id}`

Fetch metadata for one game. Returns
`{ game_id, sources_tried, sources_succeeded, fields_updated,
cover_downloaded }`.

### `POST /api/metadata/fetch-all`

Walk every game and fetch metadata for those that look incomplete
(placeholder name, no cover, no description). Returns a summary:
`{ total_games, updated, skipped, errors, details: [...] }`. Rate-limited
internally to ~3 requests per second to be polite to upstream APIs.

### `GET /api/metadata/search?q=<query>`

Search the Steam Store for matching games. Returns `{ results: [{ name,
steam_app_id, cover_url, source }] }`. Used by manual game-creation flows.

---

## Settings

### `GET /api/settings`

Returns the consolidated settings object (env-derived config + DB-stored
preferences + library stats). Secrets are redacted as `has_*` booleans.

```json
{
  "base_url": "...",
  "library_dir": "/data/library",
  "auth_disabled": false,
  "import_rate_limit_ms": 1000,
  "thumbnail_quality": 85,
  "max_upload_size_mb": 50,
  "token_expiry_days": 30,
  "has_steam_api_key": true,
  "has_steamgriddb_api_key": false,
  "has_igdb_credentials": false,
  "library_size": "12.4 GB",
  "library_size_bytes": 13311234567,
  "game_count": 47,
  "screenshot_count": 12345,
  "annotation_count": 89,
  "active_share_count": 4,
  "import_session_count": 12,
  "specialk_path": "/data/specialk",
  "tone_map_algorithm": "reinhard",
  "tone_map_exposure": 1.0
}
```

### `PUT /api/settings/api-keys`

Save a secret. Body: `{ "key_name": "...", "value": "..." }`. Allowed
`key_name`: `steam_api_key`, `steamgriddb_api_key`, `igdb_client_id`,
`igdb_client_secret`. Empty values rejected. DB value overrides any env
var with the same name.

### `DELETE /api/settings/api-keys/{key_name}`

Remove a saved secret. Falls back to env var if one exists.

### `PUT /api/settings/preferences`

Save Special K + HDR preferences. Body (all fields optional, at least one
required):

```json
{
  "specialk_path": "/data/specialk",
  "tone_map_algorithm": "reinhard",
  "tone_map_exposure": 1.0
}
```

`tone_map_algorithm` must be one of `reinhard`, `aces`, `clip`.
`tone_map_exposure` is bounded `[0.05, 8.0]`.

Setting `specialk_path` to an empty string clears the preference.

---

## Sharing

### `POST /api/screenshots/{id}/share?expires_in_days=<n>`

Create or get an active share link. Returns
`{ id, screenshot_id, token, is_active, expires_at, view_count, created_at,
url }`. If a link already exists for the screenshot, the existing one is
returned (no new token generated). `expires_in_days` is optional — omit
for no expiration.

### `GET /api/screenshots/{id}/share`

Get the active link for a screenshot, or `null` if none.

### `DELETE /api/screenshots/{id}/share`

Deactivate the link. Returns 404 if there's no active link.

### `GET /share/{token}` — *public, returns HTML*

Renders the share page (full-size image, game info, annotation,
OpenGraph + Twitter Card meta tags). Increments view count. 404 if the
token is unknown or expired.

### `GET /share/{token}/image` — *public, redirect*

Redirects to `/api/screenshots/{id}/image` so OpenGraph crawlers can
fetch the image directly.

---

## Public Gallery

These endpoints don't require auth — they expose only games where
`is_public = 1`.

### `GET /api/gallery/games?sort=name`

List public games. Same shape as `/api/games`.

### `GET /api/gallery/games/{id}`

Get a public game by ID. 404 if private.

### `GET /api/gallery/games/{id}/screenshots?page=1&limit=50&sort=date_desc`

Paginated screenshots for a public game.

### `GET /api/gallery/games/{id}/cover`

Cover image, with `Cache-Control: public, max-age=86400` so CDNs can
cache it.

### `GET /api/gallery/screenshots/{id}/image`

Full image — only if the game is public. Same caching headers as cover.

### `GET /api/gallery/screenshots/{id}/thumb/{sm|md}`

Thumbnail — only if the game is public.

---

## Server-Sent Events

Three endpoints stream progress: `/api/upload/progress/{task_id}`,
`/api/steam/import/{session_id}/progress`,
`/api/specialk/import/{session_id}/progress`.

Common protocol:

- `Content-Type: text/event-stream`
- 30-second keepalive comments (`: keepalive\n\n`) to keep proxies happy
- `X-Accel-Buffering: no` to disable nginx response buffering
- Steam + Special K use named events (`event: <type>\ndata: <json>\n\n`)
- Upload uses the default-event format (`data: <json>\n\n`); type is
  inside the JSON

Since EventSource cannot send custom headers, the JWT must be passed via
the `?token=<jwt>` query parameter. Helper:

```ts
import { tokenQs } from '@/api/client';
const url = `/api/specialk/import/${sessionId}/progress${tokenQs()}`;
new EventSource(url);
```

When you receive the `done` event (Steam/Special K) or a `complete` event
(upload), close the EventSource.
