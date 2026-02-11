"""GameVault FastAPI application."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.database import close_db, init_db
from backend.routers import (
    auth,
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
    await init_db()
    yield
    # Shutdown
    await close_db()


app = FastAPI(
    title="GameVault",
    description="Self-hosted game screenshot manager",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS (allow frontend dev server)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
app.include_router(auth.router)
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
