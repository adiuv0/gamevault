"""GameVault FastAPI application."""

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

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

from backend.config import settings
from backend.database import close_db, init_db

logger = logging.getLogger(__name__)

DEFAULT_SECRET_SENTINEL = "change-me-to-a-random-string"


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
    gallery,
    games,
    metadata,
    screenshots,
    search,
    settings as settings_router,
    share,
    steam_import,
    timeline,
    upload,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    # Startup
    settings.library_dir.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _ensure_secret_key()
    await init_db()

    if settings.disable_auth:
        logger.warning(
            "Authentication is DISABLED (GAMEVAULT_DISABLE_AUTH=true). "
            "All endpoints are publicly accessible. Only use this behind "
            "a trusted reverse proxy with its own authentication."
        )

    yield
    # Shutdown
    await close_db()


app = FastAPI(
    title="GameVault",
    description="Self-hosted game screenshot manager",
    version="0.1.0",
    lifespan=lifespan,
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

# API routers
app.include_router(auth.router)
app.include_router(gallery.router)
app.include_router(games.router)
app.include_router(screenshots.router)
app.include_router(upload.router)
app.include_router(steam_import.router)
app.include_router(timeline.router)
app.include_router(search.router)
app.include_router(share.router)
app.include_router(metadata.router)
app.include_router(settings_router.router)


# Health check (public, no auth)
@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


# Serve frontend static files (production build)
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    # Serve static assets
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    # Serve index.html for all non-API, non-share routes (SPA fallback)
    from fastapi.responses import FileResponse

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve the SPA for all non-API routes."""
        # Don't intercept API or share routes
        if full_path.startswith("api/") or full_path.startswith("share/"):
            return {"detail": "Not found"}

        # Try to serve a static file first
        static_path = frontend_dist / full_path
        if static_path.is_file() and not full_path == "":
            return FileResponse(static_path)

        # Fall back to index.html for SPA routing
        return FileResponse(frontend_dist / "index.html")
