# GameVault Security Audit

Date: 2026-04-27

Scope: local source tree at `C:\Users\Emiliano Zazueta\projects\gamevault`.

This review did not modify application source code. It inspected the backend,
frontend, CLI, Docker/deployment files, tests, and docs, then ran dependency and
static-analysis checks.

## Project Function

GameVault is a self-hosted game screenshot manager. It provides a FastAPI
backend, SQLite database, React/Vite frontend, Docker/Unraid deployment, and a
standalone sync CLI. Core capabilities include:

- Importing Steam screenshots from a Steam Community profile, optionally using
  pasted Steam cookies and API keys.
- Uploading screenshots manually and generating thumbnails.
- Importing local Special K screenshot folders, including HDR/JXR captures.
- Organizing screenshots by game, timeline, search, annotations, favorites, and
  metadata from Steam, SteamGridDB, and IGDB.
- Creating public share links and a public gallery.
- Single-user password setup with bcrypt and JWT bearer tokens.

The main threat model is a single-user self-hosted app that may be reachable on
a LAN or exposed through a reverse proxy. The most sensitive surfaces are
screenshots, EXIF metadata, share links, API tokens, Steam cookies, local
filesystem paths, and the JWT used by the web UI and sync CLI.

## Methodology

Reviewed source areas:

- Backend entry point, auth, routers, services, migrations, templates.
- Frontend API client, auth store, annotation rendering, gallery/viewer flows.
- Dockerfile, compose file, entrypoint, Unraid template, ignore files.
- CLI sync script.
- Tests and docs for intended behavior.

Tooling run:

- `npm audit --omit=dev --json`: no known vulnerabilities in production npm
  dependency graph.
- `npm audit --json`: 8 dev/build-chain advisories, including Vite, Rollup,
  picomatch, minimatch, flatted, brace-expansion, ajv, and postcss.
- `pip-audit -r requirements.txt`: no known vulnerabilities in the resolved
  Python dependency set at audit time.
- `bandit -r backend cli -f json`: no high-severity findings; 16 medium and 17
  low findings, mostly dynamic SQL construction warnings and broad exception
  handling warnings. Several SQL warnings appear to be false positives today
  because column/order fragments are selected from internal allowlists, but they
  remain worth guarding with explicit field allowlists.

Note: Python dependencies are specified as lower bounds rather than an exact
lockfile, so `pip-audit` audited the current resolution, not a reproducible
image build.

## Findings Summary

| ID | Severity | Area | Concern |
| --- | --- | --- | --- |
| GV-001 | High | Annotations/share pages | Stored XSS via raw Markdown HTML rendered into the DOM |
| GV-002 | High | Public gallery | New games are public by default and unauthenticated gallery APIs expose broad data |
| GV-003 | High | Upload routes | Multipart filenames can escape the temp directory and overwrite arbitrary app-writable paths |
| GV-004 | High | Upload/image processing | Upload size checks are bypassable when `UploadFile.size` is absent; files are fully buffered in memory |
| GV-005 | Medium | Auth tokens | JWTs are stored in `localStorage` and routinely placed in query strings |
| GV-006 | Medium | Auth endpoints | `change-password` is public and not rate-limited |
| GV-007 | Medium | Share links | Public share image flow points at authenticated image routes and does not serve token-bound images directly |
| GV-008 | Medium | File serving/deletion | DB-stored file paths are trusted without containment checks |
| GV-009 | Medium | Security headers | SPA/share responses lack a site-level CSP and other standard hardening headers |
| GV-010 | Medium | Local filesystem import | Special K import can scan/copy arbitrary server-readable paths if auth is disabled or compromised |
| GV-011 | Medium | Secrets | API keys/JWTs are stored plaintext in SQLite or CLI config |
| GV-012 | Medium | Supply chain | `latest` Docker image use, unpinned Python dependencies, and dev dependency advisories |
| GV-013 | Low | SQL construction | Dynamic SQL patterns are mostly controlled today, but fragile |
| GV-014 | Low | Operational hardening | Login rate limit is in-memory/IP-only; Docker entrypoint recursively chowns `/data` |

## Detailed Findings

### GV-001: Stored XSS via Annotation Markdown HTML

Severity: High

Evidence:

- `backend/routers/screenshots.py:155-157` creates `MarkdownIt()` and stores
  `md.render(data.content)`.
- `frontend/src/components/screenshots/AnnotationEditor.tsx:175-177` injects
  stored HTML with `dangerouslySetInnerHTML`.
- `backend/routers/share.py:121-122` also embeds stored `content_html` directly
  into the public share page.

Local probe:

`MarkdownIt().render('<img src=x onerror=alert(1)>')` returned the same raw
HTML. The same was true for `<svg onload=...>` and raw `javascript:` links.

Impact:

An annotation can execute JavaScript in the authenticated UI and in public share
pages. In the authenticated UI, this can steal the JWT from `localStorage`,
perform API actions as the user, create/share/delete data, or alter settings.
On public share pages it can execute in visitors' browsers.

Recommendation:

- Disable raw HTML in Markdown rendering or sanitize rendered HTML with a strict
  allowlist before storing or rendering.
- Prefer rendering Markdown client-side with a sanitizer such as DOMPurify, or
  server-side with bleach/nh3 and a very small tag/attribute allowlist.
- Add a restrictive CSP after sanitization; CSP should be defense in depth, not
  the primary XSS control.
- Re-render or purge existing `content_html` values after the sanitizer is
  introduced.

### GV-002: Public Gallery Defaults Expose Screenshots

Severity: High

Evidence:

- `backend/auth.py:75-81` explicitly exempts `/api/gallery/*` from auth.
- `backend/migrations/m002_gallery.py:7-8` adds `is_public INTEGER NOT NULL
  DEFAULT 1`.
- `backend/routers/gallery.py:60-74` exposes public game screenshots without
  auth.
- `backend/services/screenshot_service.py:37-42` returns `SELECT s.*` for the
  screenshot list used by both admin and public gallery flows.

Impact:

Every newly imported or created game is public unless the user later toggles it
private. If an instance is exposed to a network where auth is expected to protect
the library, the gallery endpoints can disclose screenshots and associated
metadata. Because `SELECT s.*` is returned, public gallery responses may expose
internal file paths, hashes, Steam screenshot IDs/descriptions, EXIF JSON, and
other fields that are not necessary for public viewing.

Recommendation:

- Change the default to private (`is_public DEFAULT 0`) unless the product
  intentionally prioritizes public showcase behavior over privacy.
- Make the first-run UI explicitly choose the gallery default.
- Use separate response schemas for public gallery endpoints. Return only fields
  needed by the gallery UI.
- Strip or avoid exposing EXIF data, SHA-256 hashes, internal relative paths,
  and non-display operational fields from public API responses.

### GV-003: Upload Filename Path Traversal / Arbitrary Overwrite

Severity: High

Evidence:

- `backend/routers/upload.py:49-52` writes `temp_dir / f.filename` directly.
- `backend/routers/upload.py:114-117` repeats the same pattern for sync upload.

Impact:

An attacker who can call the upload endpoint can craft a multipart filename
containing path separators, `..`, or an absolute path. In Python path joining,
an absolute child path can override the intended temp directory; relative parent
segments can escape it. The server then writes uploaded bytes to that path.

In Docker, the app process can write to `/data`, so a malicious filename could
overwrite app-owned files such as the SQLite database, thumbnails, library
content, or `.secret_key`, causing denial of service or token invalidation. If
auth is disabled or the JWT is stolen via XSS, this becomes remotely reachable.

Recommendation:

- Ignore client-supplied path components. Use `Path(f.filename).name` at
  minimum, then sanitize.
- Better: generate a random temp filename independent of user input and carry
  the original display name separately.
- After building a path, resolve it and assert it is inside the intended temp
  directory before writing.

### GV-004: Upload and Image Processing DoS

Severity: High

Evidence:

- `backend/routers/upload.py:37-43` relies on `UploadFile.size` when present.
- `backend/routers/upload.py:51` and `116` call `await f.read()`, buffering the
  entire upload into memory before writing.
- Image validation and thumbnailing process the full image after upload.

Impact:

If `UploadFile.size` is missing or inaccurate, the configured max upload size can
be bypassed. The app then buffers the entire request body in memory and writes it
to disk. Even under the byte limit, highly compressed images or very large pixel
dimensions can cause expensive decode/thumbnail work.

Recommendation:

- Stream uploads to disk in chunks while counting bytes and stop once the limit
  is exceeded.
- Enforce request size at the reverse proxy and ASGI layer where possible.
- Add pixel/dimension limits and configure Pillow decompression-bomb handling.
- Limit concurrent upload and thumbnail jobs.

### GV-005: JWTs in localStorage and Query Strings

Severity: Medium

Evidence:

- `frontend/src/stores/authStore.ts:18` and `23` read/write
  `gamevault_token` in `localStorage`.
- `backend/auth.py:106-109` accepts `?token=` for any protected route, not only
  SSE.
- `frontend/src/api/client.ts:122-126` uses query-string tokens for SSE.
- `frontend/src/api/client.ts:158-164` and
  `frontend/src/api/screenshots.ts:33-35` use query-string tokens for image and
  download URLs.

Impact:

Any XSS can steal the long-lived JWT. Query-string tokens can leak through
browser history, logs, reverse proxy access logs, diagnostics, and referrers
depending on page structure and proxy configuration.

Recommendation:

- Prefer short-lived access tokens and refresh via HttpOnly, Secure, SameSite
  cookies, or use server-side sessions.
- If query-string auth is unavoidable for SSE or image tags, issue scoped,
  short-lived, single-purpose URL tokens instead of accepting the full JWT.
- Restrict `?token=` acceptance to the specific endpoints that require it.
- Reduce default token lifetime from 30 days for internet-exposed deployments.

### GV-006: Public `change-password` Endpoint Without Rate Limiting

Severity: Medium

Evidence:

- `backend/auth.py:75-81` exempts all `/api/auth/*` paths from auth.
- `backend/routers/auth.py:133-145` changes password based only on the current
  password and has no rate limit.
- Login has per-IP failed-attempt tracking at `backend/routers/auth.py:80-89`;
  password change does not.

Impact:

Attackers can brute-force the current password through the change-password
endpoint and, if successful, replace it. This bypasses the login endpoint's
rate-limiting path. Password minimum length is only six characters.

Recommendation:

- Require a valid JWT for password change in addition to the current password.
- Apply the same or stricter rate limiting as login.
- Increase minimum password length and consider zxcvbn-style strength checks.

### GV-007: Share Links Do Not Serve Token-Bound Images Directly

Severity: Medium

Evidence:

- `backend/routers/share.py:76` uses `/api/screenshots/{id}/image` as the share
  page image URL.
- `backend/routers/share.py:90-100` validates the share token, then redirects to
  `/api/screenshots/{id}/image`.
- `/api/screenshots/{id}/image` remains auth-protected outside the share route.

Impact:

Public share pages are intended to expose a screenshot to anyone with the token,
but the image URL points at an authenticated route. That makes shares and Open
Graph previews unreliable. A future "fix" that simply makes `/api/screenshots`
public would likely overexpose private screenshots.

Recommendation:

- Serve the image directly from `/share/{token}/image` after token validation.
- Avoid redirecting to auth-protected or globally public screenshot routes.
- Keep share image URLs token-bound and apply `Cache-Control` based on desired
  privacy semantics.

### GV-008: DB-Stored File Paths Are Trusted

Severity: Medium

Evidence:

- `backend/routers/screenshots.py:62-64` deletes `settings.library_dir /
  rel_path`.
- `backend/routers/screenshots.py:87-88` serves `file_path`.
- `backend/routers/screenshots.py:113-118` serves thumbnail paths.
- `backend/routers/gallery.py:110-116` similarly serves public gallery images.

Impact:

If an attacker can poison the database, exploit upload path traversal, or trigger
a future bug that writes unsafe relative paths, file serving and deletion can
escape the library directory. This is a defense-in-depth issue today, but it
becomes more serious when combined with GV-003.

Recommendation:

- Centralize path resolution in a helper that resolves the final path and
  verifies it remains under `settings.library_dir`.
- Reject absolute paths and paths containing parent traversal before file serve,
  deletion, or thumbnail fallback.

### GV-009: Missing Security Headers

Severity: Medium

Evidence:

- `backend/main.py:138-151` serves SPA files and `index.html` without explicit
  security headers.
- `backend/routers/share.py:79-87` returns share pages without explicit security
  headers.
- Some image responses do include `X-Content-Type-Options` and `CSP:
  default-src 'none'`, which is good.

Impact:

The lack of a site-level CSP, frame restrictions, referrer policy, and similar
headers gives XSS and clickjacking bugs more room to cause damage. This is
especially relevant because annotations currently allow stored XSS.

Recommendation:

- Add a CSP after fixing annotation sanitization. A starting point might limit
  scripts to self, disallow object/embed, set `base-uri 'self'`, and frame
  ancestors to none or trusted origins.
- Add `X-Content-Type-Options: nosniff`, `Referrer-Policy`, and
  `X-Frame-Options` or `frame-ancestors`.
- Add HSTS at the reverse proxy for HTTPS deployments.

### GV-010: Server-Side Local Filesystem Import Is Powerful

Severity: Medium

Evidence:

- `backend/routers/specialk_import.py:33-40` accepts an arbitrary path and
  checks only that it exists and is a directory.
- The Special K service recursively scans top-level child directories and copies
  supported image files into the library.

Impact:

This is arguably an admin feature, but it lets whoever has API access enumerate
and copy server-readable `.png` and `.jxr` files from arbitrary directories. If
auth is disabled, misconfigured, or compromised, this becomes a local file
disclosure primitive for image-like files and a disk/CPU DoS vector.

Recommendation:

- Restrict import roots to configured allowlisted directories.
- Hide or disable this feature when auth is disabled unless a reverse proxy auth
  signal is explicitly configured.
- Avoid returning absolute server paths in API responses.

### GV-011: Secrets Stored Plaintext

Severity: Medium

Evidence:

- API keys are inserted directly into `app_settings` at
  `backend/routers/settings.py:138-143`.
- The CLI saves `"token": token` to `~/.gamevault_sync.json` at
  `cli/gamevault_sync.py:1000-1006`, using ordinary JSON write at
  `cli/gamevault_sync.py:502-503`.

Impact:

Anyone with access to the SQLite database, backups, or the CLI config file can
recover API keys and JWTs. This may be acceptable for a self-hosted single-user
app, but it should be documented clearly and protected operationally.

Recommendation:

- Do not store long-lived JWTs in the CLI config by default; prefer prompting,
  OS keyring storage, or file mode hardening.
- Consider encrypting stored API keys with an external secret, or at minimum
  document that database backups contain secrets.
- Ensure backup docs include `.secret_key`, DB, and config secrecy guidance.

### GV-012: Supply-Chain and Build Reproducibility Gaps

Severity: Medium

Evidence:

- `docker-compose.yml` uses `adiuv0/gamevault:latest`.
- `pyproject.toml:6-23` and `requirements.txt` use lower-bound Python
  dependency constraints, not exact pins or hashes.
- `frontend/package.json:25-36` includes current dev dependencies; full
  `npm audit` reports advisories in the dev/build chain. Production npm audit
  is clean.

Current audit highlights:

- `vite@7.3.1` has high/moderate advisories affecting dev server/file access.
- `rollup@4.57.1` is flagged for arbitrary file write via path traversal.
- `picomatch@4.0.3`, `minimatch`, `flatted`, `brace-expansion`, `ajv`, and
  `postcss` have dev/transitive advisories.

Impact:

The shipped Docker image may not be reproducible from the same dependency set
over time. Dev server vulnerabilities matter if `npm run dev` is exposed beyond
localhost or used on a machine with sensitive files.

Recommendation:

- Pin Docker deployments by immutable digest rather than `latest`.
- Use exact Python pins and a lockfile or hash-checked requirements for releases.
- Update dev/build dependencies, especially Vite and Rollup.
- Avoid exposing Vite dev/preview servers to untrusted networks.

### GV-013: Dynamic SQL Construction Is Fragile

Severity: Low

Evidence:

Bandit flagged dynamic SQL in game, screenshot, search, timeline, Steam import,
and Special K services. In current code, the most visible query fragments are
mostly selected from hardcoded maps or internal field dictionaries.

Impact:

This does not look directly exploitable in the reviewed routes, but it is easy
for future edits to accidentally pass user-controlled field names or order
clauses into these helpers.

Recommendation:

- Keep explicit allowlists for every dynamic column, table, and order fragment.
- Avoid generic `**fields` SQL builders on data that might later come directly
  from request bodies.
- Add tests that malicious sort/filter values fall back safely.

### GV-014: Operational Hardening Notes

Severity: Low

Observations:

- Login rate limiting is in-memory and keyed to `request.client.host`, so it
  resets on restart and may be ineffective behind a reverse proxy or distributed
  deployment.
- `entrypoint.sh:8` runs `chown -R gamevault:gamevault /data` on every start.
  This is convenient but can be slow on large libraries and should be considered
  carefully with unusual mounts.
- The local `.env` file exists but is not tracked by git, and `.dockerignore`
  excludes `.env` and `data/`. That is good.

Recommendations:

- Document reverse proxy rate-limit configuration for exposed deployments.
- Consider proxy-aware client IP handling only when trusted proxy settings are
  configured.
- Consider making recursive ownership fixes optional for large libraries.

## Positive Findings

- Auth uses bcrypt for password hashing.
- JWT signing key is auto-generated and persisted if the sentinel value is left
  unchanged.
- Docker runtime drops to a non-root `gamevault` user after entrypoint setup.
- Steam image download has a host allowlist for known Steam domains.
- Uploaded images are checked by actual detected format before final library
  storage, reducing extension-based stored XSS risk for uploaded files.
- Many SQL values are parameterized; the main remaining SQL concern is dynamic
  identifiers/fragments rather than raw value interpolation.
- `.env` and `data/` are ignored by git and excluded from Docker build context.
- Production npm dependency audit and Python dependency audit were clean at the
  time of review.

## Suggested Remediation Order

1. Fix annotation sanitization and purge/re-render existing `content_html`.
2. Make gallery private by default and slim public gallery response schemas.
3. Fix upload temp filename handling and stream uploads with hard byte limits.
4. Stop accepting full JWTs in arbitrary query strings; issue scoped URL tokens
   where needed.
5. Require auth and rate limiting for password changes.
6. Serve share images directly from token-bound share routes.
7. Add library path containment checks before file serve/delete.
8. Add security headers and reverse-proxy deployment guidance.
9. Update dev/build dependencies and introduce reproducible Python dependency
   locking for releases.

