"""GameVault FastAPI application."""

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI

# Configure logging so our INFO messages appear in container logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Keep noisy libraries at WARNING
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders

from backend.config import DEFAULT_SECRET_SENTINEL, settings
from backend.database import close_db, database_session, init_db
from backend.services.duplicate_service import resume_scan_job, stop_scan_job
from backend.services.import_job_service import start_import_worker, stop_import_worker
from backend.services.performance_diagnostics import MediaTimingMiddleware
from backend.version import APP_VERSION

# ── Per-context Content-Security-Policy strings ──────────────────────────────
#
# Three contexts:
#
#   1. Public share pages (/share/...) — minimal HTML, inline <style> in
#      the template, no JS. Only same-origin images allowed (the share
#      route now serves images directly).
#   2. SPA + admin API responses — Vite-built bundle, Tailwind, React.
#      Lucide icons render as inline SVG so no img/font fetches.
#   3. Image/file responses (set explicitly elsewhere as ``default-src 'none'``)
#      — left alone by the middleware.
#
# Inline ``<style>`` is kept allowed for both contexts because:
#   - the share template ships inline CSS (acceptable single-file output)
#   - some Tailwind + lucide combinations rely on tiny inline style attrs
# Inline ``<script>`` is forbidden everywhere.

_CSP_SHARE = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

_CSP_SPA = (
    "default-src 'self'; "
    "img-src 'self' data: blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "font-src 'self' data:; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


class SecurityHeadersMiddleware:
    """Apply standard security response headers across the whole app.

    Closes GV-009 from the audit. Notes on the design:
      * Headers are ``setdefault``-style — if a route already set its own
        Content-Security-Policy (e.g. image responses use the strict
        ``default-src 'none'``), we don't override it.
      * CSP is route-aware: the public ``/share/`` pages get a stricter
        policy than the SPA + admin pages.
      * Pure ASGI rather than ``BaseHTTPMiddleware``: only the response start
        message is touched, so streamed originals pass through untouched
        instead of being relayed chunk by chunk through an extra task.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        csp = _CSP_SHARE if scope.get("path", "").startswith("/share/") else _CSP_SPA

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                # Set if absent — never clobber stricter route-level headers.
                for name, value in (
                    ("X-Content-Type-Options", "nosniff"),
                    ("X-Frame-Options", "DENY"),
                    ("Referrer-Policy", "strict-origin-when-cross-origin"),
                    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
                    ("Content-Security-Policy", csp),
                ):
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)

logger = logging.getLogger(__name__)

def _ensure_secret_key() -> None:
    """Auto-generate a persistent secret key if the user hasn't set one."""
    if settings.secret_key != DEFAULT_SECRET_SENTINEL:
        return  # User explicitly set GAMEVAULT_SECRET_KEY — use it as-is

    key_file = settings.data_dir / ".secret_key"
    if key_file.exists():
        stored = key_file.read_text().strip()
        if stored:
            settings.secret_key = stored
            logger.info("Loaded auto-generated secret key from %s", key_file)
            return

    # Generate a new random key and persist it
    new_key = secrets.token_hex(32)
    key_file.write_text(new_key)
    settings.secret_key = new_key
    logger.warning(
        "Generated new secret key (saved to %s). "
        "Set GAMEVAULT_SECRET_KEY env var to use your own.",
        key_file,
    )
from backend.routers import (
    auth,
    duplicates,
    gallery,
    games,
    metadata,
    screenshots,
    search,
    share,
    specialk_import,
    steam_import,
    timeline,
    upload,
)
from backend.routers import settings as settings_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    # Startup
    settings.library_dir.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _ensure_secret_key()
    await init_db()
    await start_import_worker()
    await resume_scan_job()

    if settings.disable_auth:
        logger.warning(
            "Authentication is DISABLED (GAMEVAULT_DISABLE_AUTH=true). "
            "All endpoints are publicly accessible. Only use this behind "
            "a trusted reverse proxy with its own authentication."
        )

    try:
        yield
    finally:
        # Stop the worker before closing SQLite. Running jobs are requeued on startup.
        await stop_import_worker()
        await stop_scan_job()
        await close_db()


app = FastAPI(
    title="GameVault",
    description="Self-hosted game screenshot manager",
    version=APP_VERSION,
    lifespan=lifespan,
    dependencies=[Depends(database_session)],
)

# CORS — localhost defaults plus any extra origins from GAMEVAULT_CORS_ORIGINS
_cors_origins = ["http://localhost:5173", "http://localhost:8080"]
if settings.cors_origins:
    _cors_origins.extend(o.strip() for o in settings.cors_origins.split(",") if o.strip())
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Apply security headers to every response. Order matters: this runs
# *outside* the CORS middleware, so headers we add are not consumed by
# CORS preflight handling.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(MediaTimingMiddleware)

# API routers
app.include_router(auth.router)
app.include_router(gallery.router)
app.include_router(games.router)
app.include_router(screenshots.router)
app.include_router(upload.router)
app.include_router(steam_import.router)
app.include_router(specialk_import.router)
app.include_router(timeline.router)
app.include_router(search.router)
app.include_router(share.router)
app.include_router(metadata.router)
app.include_router(settings_router.router)
app.include_router(duplicates.router)


# Health check (public, no auth)
@app.get("/api/health")
async def health():
    from backend.services.instance_service import get_instance_id

    body = {"status": "ok", "version": APP_VERSION}
    # Lets GameVault Sync tell this server from another one at the same address.
    instance = await get_instance_id()
    if instance:
        body["instance"] = instance
    return body


def mount_frontend(application: FastAPI, frontend_dist: Path) -> None:
    """Mount the production SPA without exposing files outside its build."""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    root = frontend_dist.resolve()
    application.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

    @application.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if full_path.split("/", 1)[0] in {"api", "share"}:
            raise HTTPException(404, "Not found")
        static_path = (root / full_path).resolve()
        if not static_path.is_relative_to(root):
            raise HTTPException(404, "Not found")
        if static_path.is_file() and full_path:
            return FileResponse(static_path)
        index_path = (root / "index.html").resolve()
        if not index_path.is_relative_to(root):
            raise HTTPException(404, "Not found")
        return FileResponse(index_path)


frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    mount_frontend(app, frontend_dist)
