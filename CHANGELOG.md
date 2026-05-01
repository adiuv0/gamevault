# Changelog

All notable changes to GameVault will be tracked in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project loosely follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Targeted as `0.2.0`. The big themes are HDR / Special K support and
closing the entire 14-finding security audit
(`docs/SECURITY_AUDIT_2026-04-27.md`). Existing installs upgrade in place
— migrations run automatically on container start.

### Added

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
