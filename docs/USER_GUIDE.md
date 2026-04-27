# GameVault User Guide

This guide walks through every feature in the GameVault web UI from a user's
perspective. For deployment and configuration, see
[CONFIGURATION.md](CONFIGURATION.md). For REST endpoint details, see
[API_REFERENCE.md](API_REFERENCE.md).

## Contents

1. [First Run & Authentication](#first-run--authentication)
2. [The Library](#the-library)
3. [Game Pages](#game-pages)
4. [Uploading Screenshots](#uploading-screenshots)
5. [Steam Import](#steam-import)
6. [Special K Import (HDR & SDR)](#special-k-import-hdr--sdr)
7. [Timeline](#timeline)
8. [Search](#search)
9. [Annotations](#annotations)
10. [Sharing](#sharing)
11. [Public Gallery](#public-gallery)
12. [Settings](#settings)
13. [GameVault Sync (Desktop CLI)](#gamevault-sync-desktop-cli)

---

## First Run & Authentication

The first time you open GameVault you'll see a setup screen asking you to
create a password. The password must be at least 6 characters. Once set, the
password is stored as a bcrypt hash in the SQLite database and never leaves
the server.

After setup the login page appears — enter the password and you'll receive a
JWT token (default lifetime: 30 days, configurable). The token is stored in
browser `localStorage` as `gamevault_token` and sent as a `Bearer` header on
every API request.

**Disabling auth.** For LAN-only setups behind a trusted reverse proxy, set
`GAMEVAULT_DISABLE_AUTH=true` and GameVault will skip all authentication
checks. Use with care — the entire admin UI becomes public.

**Password change.** Settings → Security → "Change Password" requires the
current password.

**Rate limiting.** After 5 failed login attempts from the same IP within 15
minutes, further attempts are rejected with HTTP 429 until the window
expires.

---

## The Library

The Library page (`/`, the default landing page) lists every game you have
screenshots for. Each game shows a cover image, screenshot count, and the
date of the most recent screenshot.

**Views.** Toggle between grid (default) and list views via the top bar. The
choice is remembered per browser.

**Sorting.** Sort by name, date of newest screenshot, or screenshot count.
Like the view mode, this preference is saved locally.

**Add Game.** "Add Game" creates an empty game record so you can upload to
it. Games are also created automatically by the Steam and Special K
importers, and by the upload page.

**Fix Metadata.** This button walks every game in your library and fetches
missing metadata (real game name, cover art, description, developer,
release date) from Steam Store API → SteamGridDB → IGDB in priority order.
Games that already look complete are skipped. Useful after a large import
where games were created with placeholder names like `App 1234567`.

**Clean Up Empty.** Deletes every game record that has zero screenshots,
which sometimes happens when a sync is cancelled mid-run. Confirms before
deleting.

---

## Game Pages

Click any game tile to open its page. You'll see:

- **Cover and metadata** — name, developer, release date, genres, description
- **Screenshot grid** — all screenshots for the game, paginated (50 per page)
- **Sort options** — newest first (default), oldest first, by filename
- **Refresh metadata** — re-fetches from Steam / SteamGridDB / IGDB if you
  added an API key after the game was first imported
- **Edit** — change the game's name, developer, publisher, release date,
  genres, or description manually

Click any screenshot thumbnail to open the gallery viewer. From there you
can:

- Page through screenshots with arrow keys
- Toggle favorite (star icon)
- Open the annotation editor (note icon)
- Generate or copy a share link
- Download the original file (handy for HDR JXR/PNG)
- Delete the screenshot

---

## Uploading Screenshots

The Upload page (`/upload`) accepts drag-and-drop or click-to-browse.
Select a destination game, drop in files, and progress streams back over
Server-Sent Events.

**Allowed formats:** JPEG, PNG, WebP, BMP, TIFF, GIF, JXR.

**Per-file limit:** 50 MB by default (configurable via
`GAMEVAULT_MAX_UPLOAD_SIZE_MB`).

**Duplicate handling.** Each file's SHA-256 hash is checked against existing
screenshots. If a duplicate is found the file is skipped and you're told
which existing entry it matched. The hash check happens before thumbnails
are generated, so duplicates cost almost nothing.

**EXIF & date.** Date taken is extracted from EXIF (`DateTimeOriginal`,
`DateTimeDigitized`, `DateTime` in priority order). For JXR there's no
accessible EXIF, so the file's mtime is used.

**Thumbnails.** Two JPEG thumbnails are generated per upload — 300px wide
for grid views and 800px wide for the gallery preview. HDR sources (JXR and
16-bit PNG) are tone-mapped to SDR before thumbnail generation. The
original file is preserved untouched.

---

## Steam Import

The Steam Import page (`/import/steam`) imports your Steam Community
screenshots in four steps.

### Step 1 — Profile + cookies

Enter your **Steam ID** or **custom URL** (the part after
`steamcommunity.com/id/`). Optional **steamLoginSecure** and **sessionid**
cookies unlock private screenshots — paste them from your browser's
DevTools (Application → Cookies → `steamcommunity.com`). Cookies are sent
to Steam servers only; GameVault does not store them.

**Steam API key (recommended).** Add a free Steam Web API key in Settings
to use the official `IPublishedFileService/GetUserFiles` endpoint, which is
faster and more accurate than HTML scraping. With a key set, screenshot
counts shown during the game-selection step are exact. Without one, the
importer falls back to HTML scraping the screenshot grid pages.

### Step 2 — Validate

GameVault confirms the profile exists and reads its display name + avatar.
On success it auto-discovers every game with public screenshots on the
profile and shows the list.

### Step 3 — Game selection

Pick which games to import. By default all are selected. Use the filter box
when there are many games. Each row shows the screenshot count when known.

### Step 4 — Import with live progress

The progress bar shows overall completion across all games, plus per-game
counters for imported / skipped / failed. The import can be cancelled at
any time — already-imported screenshots are kept; the rest are abandoned.

**Duplicate detection.** During import, a screenshot is skipped if either
its Steam ID or its content hash matches an existing entry — this means
re-running the importer is safe and idempotent.

**Cookie expiration.** If cookies expire mid-import, you'll see auth
errors in the per-game progress and the import will continue with whatever
games it can still reach. Re-extract cookies and run the importer again to
fill in the rest.

---

## Special K Import (HDR & SDR)

The Special K Import page (`/import/specialk`) imports screenshots that
[Special K](https://www.special-k.info/) writes to disk locally. Unlike the
Steam importer, this reads directly from the filesystem the GameVault
*server* can see — no cookies, no rate limits.

### How it works

You provide a **root path**. GameVault scans that path's top-level
subdirectories — each subdirectory is treated as a separate game. Inside
each game folder, JXR and PNG files are collected recursively (so it works
regardless of whether Special K is set to `<game>/HDR/`,
`<game>/Screenshots/HDR/`, or just `<game>/`).

The folder name becomes the game name after light cleanup:

- `.exe` suffix is stripped
- `CamelCase` is split into separate words
- Letter→digit boundaries are split (so `Cyberpunk2077` → `Cyberpunk 2077`)

If your folder names don't clean up nicely, you can rename the game in
GameVault later.

### HDR handling

JXR files (and 16-bit HDR PNGs) cannot be displayed natively by web
browsers. GameVault's solution:

1. The original file is copied to your library untouched
2. SDR JPEG thumbnails are generated by tone-mapping the HDR source
3. The gallery shows the SDR thumbnail; downloading gives you the original

The tone-mapping algorithm and exposure are configured in Settings
(see [HDR & Tone Mapping](#hdr--tone-mapping) below).

### Step-by-step

1. Open `/import/specialk`. If you've used the page before, the path is
   pre-filled from your saved preference.
2. Enter the root path **as the GameVault server sees it**. For Docker
   installs, this means the path *inside* the container — bind-mount your
   host Special K folder and use the container path here.
3. Click **Scan path**. The scanner returns the list of detected games
   with HDR/SDR badges and screenshot counts.
4. Pick which games to import (defaults to all).
5. Click **Import**. The same SSE progress UI as Steam Import shows live
   per-game progress; you can cancel at any time.

### What gets stored

Each imported screenshot:

- Original file is saved to `library/<game>/screenshots/specialk_<timestamp>_<original-stem>.<ext>`
- SHA-256 hash is computed and stored — re-running the importer skips
  files already in the database
- `taken_at` is the file's mtime (no EXIF for JXR)
- Source is recorded as `specialk_import`
- Auto-metadata fetch runs once per new game (so you get covers, etc.)

### Re-running

Running the importer again on the same path is safe — duplicates are
detected by hash. New screenshots Special K has written since the last run
will be picked up. You can keep this as a periodic sync workflow.

---

## Timeline

The Timeline page (`/timeline`) shows your screenshots grouped by day,
newest first, with each day expanded into a strip of thumbnails.

**Filters.** Date range (start/end) and per-game. The stats panel at the
top shows totals — earliest/latest screenshot, total days with activity,
total games.

**Pagination.** Loads 30 days at a time; "Load more" fetches the next
batch.

This view is great for "what did I play last weekend" or for browsing a
specific period.

---

## Search

The Search page (`/search`) runs full-text search across every screenshot
using SQLite's FTS5 with BM25 ranking.

**What's indexed:**

- Game name (weight 5x)
- Filename (weight 1x)
- Steam description if any (weight 3x)
- Annotation content (weight 10x — annotations rank highest)

Each query word is treated as a prefix, so `cyber` matches `cyberpunk`.
Hyphens are tokenizer separators.

**Filters.** Game, date range, favorites-only.

**Sort.** Relevance (default, BM25-weighted), newest first, oldest first.

The results page shows a thumbnail, game, filename, taken-at date, and a
~150-character preview of the annotation if one exists.

---

## Annotations

Any screenshot can have a single annotation — a free-form Markdown note.
Open the gallery viewer for the screenshot and click the note icon (or
press the keyboard shortcut). The editor accepts standard Markdown; the
HTML rendering is generated server-side via `markdown-it-py` and stored
alongside the raw text.

Annotations are indexed in FTS5 and contribute most heavily to search
relevance, so they're the best place to write playthrough notes,
build descriptions, or screenshot context you'll want to find later.

To remove an annotation, open the editor and delete its content (or use
the Delete button).

---

## Sharing

Every screenshot can have a public share link. From the gallery viewer:

1. Click **Share**
2. Optionally set an expiration in days (no expiration by default)
3. Copy the URL — it's of the form `https://your-server/share/<token>`

The share page is a self-contained HTML page with:

- The screenshot at full resolution
- The game name and dimensions
- The annotation rendered as Markdown (if any)
- OpenGraph + Twitter Card meta tags so Discord/Slack/etc. show a rich
  preview

Share pages do **not** require authentication — anyone with the link can
view.

**Revoking.** Click **Share** again on a screenshot with an active link and
choose **Revoke**. The link becomes inactive immediately; reusing the same
URL after revocation returns a 404.

**Tracking.** Each view increments the link's view count.

---

## Public Gallery

There's a separate, no-auth-required gallery at `/gallery` that shows every
game whose `is_public` flag is `true` (the default for new games). It uses
the same gallery-viewer UI as the admin side but only exposes public games
and screenshots.

To make a game private, open it in the admin UI and toggle "Public" off.
Private games won't appear in `/gallery`, and direct URLs to their
screenshots return 404.

This is useful for building a public showcase site without exposing your
entire library.

---

## Settings

The Settings page (`/settings`) collects everything that can be configured
at runtime.

### Library Stats

Game count, screenshot count, annotation count, active share count, total
imports, and disk usage of the `library/` directory.

### Configuration (read-only)

Surfaces the current values of environment variables — base URL, library
path, max upload size, thumbnail quality, import rate limit, token expiry,
auth state.

### API Keys

Optional but recommended. Stored as plain text in the `app_settings` table.
DB values override environment variables.

| Service | Purpose | Where to get one |
|---|---|---|
| Steam Web API | Fast screenshot discovery + Steam Store metadata | [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey) |
| SteamGridDB | High-quality cover art | [steamgriddb.com/profile/preferences/api](https://www.steamgriddb.com/profile/preferences/api) |
| IGDB Client ID/Secret | Metadata for non-Steam games | [api-docs.igdb.com](https://api-docs.igdb.com/#getting-started) |

### Special K & HDR

- **Screenshots root path** — pre-fills the Special K Import page. Empty by
  default.
- **HDR tone-map algorithm** — Reinhard, ACES, or Hard clip (see [HDR &
  Tone Mapping](#hdr--tone-mapping) below).
- **Tone-map exposure** — slider from 0.1 to 4.0. 1.0 is neutral. Lower
  darkens, higher brightens. Affects all *future* HDR thumbnail
  generation; existing thumbnails aren't regenerated.

### Sync Token

Displays your current JWT for use with the GameVault Sync desktop CLI.

### Security

Change password (only shown if auth is enabled).

### HDR & Tone Mapping

HDR JXR and 16-bit PNG files store luminance values that exceed what a
normal SDR display (or browser) can represent. To show them in the
gallery, GameVault converts them to SDR using one of three algorithms:

- **Reinhard** — soft rolloff into highlights. The default. Safe for most
  content; preserves shadow detail well.
- **ACES** — filmic curve approximating cinema-grade tone mapping.
  Slightly more contrast and saturation than Reinhard. Good for cinematic
  games.
- **Hard clip** — simply clips values above 1.0. Fastest, but blows out
  highlights. Use only if the tone-mapped result looks too washed out and
  you don't care about preserving detail in bright areas.

After tone mapping, the result is encoded with the sRGB transfer function
(industry-standard for SDR displays).

The original HDR file is always preserved on disk, so changing the
algorithm later only affects newly imported screenshots' thumbnails.

---

## GameVault Sync (Desktop CLI)

`cli/gamevault_sync.py` is a separate Python script that runs on your
gaming PC and uploads new screenshots to your GameVault server. It
supports two sources, run individually or together:

- **Steam** — walks `<Steam>/userdata/<id>/760/remote/<appid>/screenshots/`
- **Special K** — walks a user-supplied root where each top-level
  subfolder is a game (handles both `.png` SDR and `.jxr` HDR captures)

Useful when:

- You want non-public Steam screenshots without pasting cookies
- You want to upload screenshots immediately after taking them
- Your GameVault server can't reach the local filesystem (e.g. a remote
  server) but you still want Special K imports
- Your GameVault server can't reach steamcommunity.com directly

The CLI has both a tkinter GUI and a headless mode. See
[../cli/README.md](../cli/README.md) for full flag reference and examples.

**Mode.** The GUI has a Mode dropdown (Steam Only / Special K Only / Both)
that toggles which path fields are required. The CLI uses
`--mode {steam,specialk,both}` (default `steam` for backward
compatibility).

**Auth token.** Copy from Settings → Sync Token, or read it from your
browser's `localStorage` under the `gamevault_token` key.

**Server-side vs client-side Special K imports.** GameVault has two ways
to import Special K screenshots:

- The **Special K Import page** (in the web UI) reads from a path on the
  GameVault *server* — useful when the server can see the Special K
  folder directly (e.g. running on the same Windows machine, or with a
  bind mount).
- The **Sync CLI in Special K mode** reads from a path on your *local
  PC* and uploads over HTTP — useful when GameVault runs remotely (e.g.
  on Unraid) and can't see your gaming PC's filesystem.

Both produce the same library entries. The CLI matches games by name
client-side, so a Special K folder named the same as a Steam-imported
game merges into the same library entry.
