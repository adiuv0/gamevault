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
- [Duplicate Review](#duplicate-review)
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
Changing the password also signs out every sync device.

### `POST /api/auth/devices/pair` — *public*

Used by GameVault Sync on first launch. Body:
`{ "password": "...", "name": "Gaming PC", "install_id": "..." }` (`install_id` optional).
Returns `{ token, device: { id, name, created_at, last_used_at } }`. The token starts with
`gvd_`, does not expire, and is shown only in this response. Signing in again with the same
`install_id` (a random id the app keeps per install) replaces that install's previous token;
two PCs that share a name each keep their own. Shares the login rate limit.

Device tokens are accepted only in the `Authorization: Bearer` header and only on the
endpoints the sync app needs: `GET /api/auth/verify`, `POST /api/screenshots/check-hashes`,
`POST /api/games/by-name`, `GET /api/games/by-steam-appid/{id}`, `POST /api/upload/sync`
and `DELETE /api/auth/devices/current`. Anything else returns 403.

### `GET /api/auth/devices` — *auth required*

Lists paired devices: `{ devices: [{ id, name, created_at, last_used_at }] }`.

### `POST /api/auth/devices` — *auth required*

Body: `{ "name": "..." }`. Creates a device token from a signed-in browser session, for
scripts or a headless sync install. Same response shape as pairing.

### `DELETE /api/auth/devices/current` — *device token*

Signs the calling device out (the sync app's **Sign out**). Returns `{ revoked: id }`;
400 when called with a browser session.

### `DELETE /api/auth/devices/{id}` — *auth required*

Revokes one device. Its next request gets 401 and the app asks to sign in again.

---

## Health

### `GET /api/health` — *public*

Returns `{ "status": "ok", "version": "1.2.0" }`, plus `instance`, a random id
created once per database. GameVault Sync uses it to tell servers apart: the
same server under another address keeps the app's memory of what it uploaded,
and a different server at the same address starts fresh. Used by Docker
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
`date_asc`, `name`. The response includes an opaque `next_cursor`. Pass it
back as `cursor=<token>` to fetch the next stable keyset page. Cursors are
bound to the selected sort and invalid tokens return HTTP 400. Numbered
`page` requests remain supported for backward compatibility.

### `POST /api/games/{id}/refresh-metadata`

Re-fetch metadata from Steam Store API → SteamGridDB → IGDB. Returns the
result of the metadata fetch (which sources succeeded, what fields were
updated).

### `GET /api/games/by-steam-appid/{appid}`

Get-or-create by Steam app ID. If the game doesn't exist, fetches the real
name from the Steam Store API and creates the record. If it exists with a
placeholder name (`App {id}`), tries to upgrade the name. Optional `?name=` is
the name the caller knows locally; it's used instead of the placeholder when the
store can't be reached. Used by GameVault Sync for Steam games.

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

Returns full logical-capture details, including `variant_count`, a `variants` array for
every preserved physical file, and a `has_annotation` flag. Viewer clients fetch this
record on demand.

Game screenshot-list endpoints paginate logical captures and return card-sized items:
`id`, `game_id`, `filename`, `media_version`, `taken_at`, `uploaded_at`, `variant_count`, and the
authenticated favorite/annotation flags. Variant metadata is intentionally omitted
from list responses.

The public equivalent, `GET /api/gallery/games/{id}/screenshots`, supports
the same `limit`, `sort`, `page`, and `cursor` contract while retaining its
slimmer public response model.

### `DELETE /api/screenshots/{id}`

Delete the screenshot record AND its files (full image + thumbnails).
Different from game deletion which leaves files alone.

### `GET /api/screenshots/{id}/image`

Serves the full-resolution image. Content-Type is detected from extension
— `.jxr` is served as `image/vnd.ms-photo` so browsers offer download
rather than trying to render. CSP headers prevent inline script execution.

Screenshot media endpoints accept the optional opaque `v` query parameter returned as
`media_version` by list and detail responses. Authenticated requests whose revision matches
the current record receive `Cache-Control: private, max-age=31536000, immutable`. Missing or
stale revisions use the backward-compatible one-day private policy.

### `GET /api/screenshots/{id}/thumb/{sm|md}`

Thumbnail. `sm` = 300px wide, `md` = 800px wide. Always `image/jpeg`.
Falls back to the full image if no thumbnail exists.

### `GET /api/screenshots/{id}/preview`

Serves the configured bounded JPEG viewer preview. While backfill is incomplete or a
cache entry is missing, it falls back to the 800px thumbnail and then the original.

### `GET /api/screenshots/{id}/full`

Serves the full-resolution image in a format browsers can display; the viewer uses it
for JPEG XR and TIFF captures. Browser-native originals are served exactly like
`/image`. JPEG XR and TIFF originals are tone-mapped (with the HDR settings) to a
full-size JPEG cached under `thumbnail_dir/<game>/full/`, made at import (and by
the preview-cache repair job for older captures) or on first request if missing. If the original can't be decoded, the preview is served.

### `POST /api/screenshots/{id}/favorite`

Toggle favorite state for every variant in the logical capture. Returns
`{ "is_favorite": bool }`.

### `GET /api/screenshots/{id}/annotation`

Get the logical capture's annotation. 404 if none.

### `POST /api/screenshots/{id}/annotation`

Create or update the logical capture's annotation. Body:
`{ "content": "<markdown>" }`.
Server renders Markdown to HTML and stores both. FTS index is updated.

### `DELETE /api/screenshots/{id}/annotation`

Remove annotations from every variant in the logical capture. FTS indexes are
updated.

### `POST /api/screenshots/check-hashes`

Bulk hash existence check. Body: `{ "hashes": ["sha256...", ...] }`.
Returns `{ "existing": [...], "new": [...] }`. Used by GameVault Sync to
avoid uploading duplicates.

### `POST /api/screenshots/{id}/share`

See [Sharing](#sharing).

---

## Duplicate Review

All duplicate-review endpoints require admin authentication. Candidate matching is
conservative: assets must belong to the same game, represent complementary HDR and
SDR variants, have identical dimensions, share a filename stem or near-identical
capture time, and have a perceptual-hash distance of eight bits or less. A match is
never deleted automatically.

### `GET /api/duplicates/scan`

Returns the latest durable scan job, or `null` when no scan has run. Job fields
include `status`, `cursor_id`, `total`, `processed`, `errors`, and timestamps.

### `POST /api/duplicates/scan`

Starts a new scan or resumes the latest incomplete or failed scan. Returns the job
immediately while work continues in the background.

### `GET /api/duplicates/candidates`

Lists review candidates. Query parameters are `status` (`pending`, `grouped`, or
`dismissed`), `page` (minimum 1), and `limit` (1-100).

### `POST /api/duplicates/candidates/{id}/group`

Places both physical assets in one logical capture group and recalculates the
game's logical screenshot count. Every original file remains intact.

### `POST /api/duplicates/candidates/{id}/dismiss`

Marks a candidate as not matching. The assets and their capture groups are
unchanged.

### `POST /api/screenshots/{id}/ungroup`

Detaches the selected physical variant from its logical capture and returns its
updated capture details. Originals, notes, favorites, and share links are retained.
The separated pairs are marked dismissed so subsequent scans do not suggest them again.

---

## Upload

### `POST /api/upload`

Multipart form upload. Fields:

- `files`: one or more files (repeated form field)
- `game_id`: integer, required
- `taken_at`: optional ISO 8601 override

Returns `{ "task_id": "<uuid>", "file_count": N }`. Use the task_id with
`/api/upload/progress/<task_id>` to stream per-file progress over SSE.

The response is returned only after the request bodies are durably staged and the
job is committed. Once a task ID has been returned, processing resumes after an
application restart without duplicating committed screenshots. Staging lives under
`GAMEVAULT_DATA_DIR/upload-staging` and is removed when the job reaches a terminal
state.

Authentication is checked before parsing. Each file is limited by
`GAMEVAULT_MAX_UPLOAD_SIZE_MB` (default 512 MiB); the entire multipart request is
limited independently by `GAMEVAULT_MAX_UPLOAD_BATCH_SIZE_MB` (default 8192 MiB).
Exceeding either limit returns HTTP 413, including for chunked requests.
`GAMEVAULT_MAX_UPLOAD_FILES` defaults to 200. `GET /api/upload/limits` returns
`max_file_mb`, `max_batch_mb`, and `max_files` for the upload UI.

**Format whitelist** (matched against magic bytes, not extension): JPEG,
PNG, WebP, BMP, TIFF, GIF, JXR.

### `POST /api/upload/sync`

Same shape as `POST /api/upload` but processes inline and returns the
final result synchronously:
`{ "uploaded": N, "screenshots": [...], "skipped": [{filename, reason}], "errors": [{filename, error}] }`.
Used by GameVault Sync, which needs to know each upload's outcome
immediately. No SSE. An optional `taken_at_hint` (ISO 8601) dates files whose
EXIF has no capture date; unlike `taken_at`, it never overrides EXIF. This endpoint is request-bound; callers can safely retry
after a connection failure because duplicate content hashes are skipped.

### `GET /api/upload/jobs/{task_id}`

Returns the durable job snapshot, including aggregate counts and per-file status.
Storage paths and reserved destination filenames are not exposed.

### `GET /api/upload/progress/{task_id}` — *SSE*

Stream of `{type, ...}` JSON events. The first event is a `snapshot` rebuilt from
durable state, so reconnecting after a restart does not lose progress. Other event
types are `start`, `file_start`,
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

Queue a durable background import. Body adds `game_ids` (empty = all) and
`is_numeric_id` to the validate body. Returns `{ "session_id": int }`. The job
is resumed after application restart; Steam cookies in the pending payload are
encrypted at rest and removed when the job reaches a terminal state.

### `GET /api/steam/import/{session_id}` — *snapshot*

Get current session status. Returns `SteamImportSessionResponse` —
includes status, total/completed games, total/completed/skipped/failed
screenshots, timestamps.

### `GET /api/steam/import/{session_id}/progress` — *SSE*

Stream of named events:

Connecting after a completed, failed, or cancelled import returns the persisted
terminal summary followed by `done`, including after an application restart.

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

Request cancellation. A queued import is cancelled immediately. Mid-flight
screenshots may still complete; new ones won't start. Returns 400 if the session
is already terminal.

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
`folder_names` imports everything found. Returns `{ "session_id": int }`. The
filesystem job is persisted and resumed after application restart.

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
  "preview_max_width": 1920,
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

### `GET /api/settings/preview-cache/backfill`

Returns the latest durable preview-cache audit, or `null` before the first run. The
job reports `status`, `total`, `processed`, `errors`, and timestamps.

### `POST /api/settings/preview-cache/backfill`

Starts a new preview-cache audit or resumes an interrupted one. Existing valid previews
are checked and skipped; missing previews are rebuilt in the background. Returns the
durable job record immediately.

### `GET /api/settings/performance`

Returns process-local aggregate timings for gallery queries, media responses, image
processing, and imports. Each metric includes its operation, count, average, p50, p95,
maximum, lifetime and recent error counts, slow count, threshold, byte total, and
last-seen timestamp. Percentiles and recent errors use at most the 256 most recent
samples; totals cover the current collection period. Paths, filenames, query strings,
and request bodies are never retained.

### `DELETE /api/settings/performance`

Clears collected timings and starts a new collection period. This changes no database,
library, cache, or import state.

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

### `GET /share/{token}/image` — *public*

Serves the shared image directly, authorized by the token alone, so
OpenGraph crawlers can fetch it. JPEG XR and TIFF captures are served as
the same full-size JPEG render as `/api/screenshots/{id}/full`.

---

## Public Gallery

These endpoints don't require auth — they expose only games where
`is_public = 1`.

### `GET /api/gallery/games?sort=name`

List public games. Same shape as `/api/games`.

### `GET /api/gallery/games/{id}`

Get a public game by ID. 404 if private.

### `GET /api/gallery/games/{id}/screenshots?page=1&limit=50&sort=date_desc`

Paginated card-sized logical captures for a public game.
Cards include an opaque `media_version` for cache-busting derivative changes.

### `GET /api/gallery/games/{id}/cover`

Cover image, with `Cache-Control: public, max-age=86400` so CDNs can
cache it.

### `GET /api/gallery/screenshots/{id}/image`

Full image — only if the game is public. Same caching headers as cover.

### `GET /api/gallery/screenshots/{id}/full`

Full resolution in a browser format, as `/api/screenshots/{id}/full` — only if the
game is public.

### `GET /api/gallery/screenshots/{id}`

Public logical-capture details for an opened viewer, including physical variants but
excluding internal paths, hashes, EXIF, source details, and private curation state.

### `GET /api/gallery/screenshots/{id}/thumb/{sm|md}`

Thumbnail — only if the game is public.

### `GET /api/gallery/screenshots/{id}/preview`

Bounded viewer preview — only if the game is public. Falls back to the 800px thumbnail
and then the original while preview backfill is incomplete.

Public media also accepts `v`, but remains `public, max-age=86400` even when the revision
matches so changing a game to private takes effect after a bounded cache lifetime.

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
(upload), close the EventSource. Upload clients should also apply the initial
`snapshot` before listening for incremental file events.
