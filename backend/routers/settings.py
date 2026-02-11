"""Application settings routes."""

from fastapi import APIRouter, Depends

from backend.auth import require_auth
from backend.config import settings
from backend.database import get_db
from backend.services.filesystem import format_file_size, get_library_size_bytes

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_auth)])


@router.get("")
async def get_settings():
    """Get application settings (secrets redacted)."""
    lib_size = get_library_size_bytes()

    # Database stats
    db = await get_db()

    cursor = await db.execute("SELECT COUNT(*) FROM games")
    game_count = (await cursor.fetchone())[0]

    cursor = await db.execute("SELECT COUNT(*) FROM screenshots")
    screenshot_count = (await cursor.fetchone())[0]

    cursor = await db.execute("SELECT COUNT(*) FROM annotations")
    annotation_count = (await cursor.fetchone())[0]

    cursor = await db.execute("SELECT COUNT(*) FROM share_links WHERE is_active = 1")
    active_share_count = (await cursor.fetchone())[0]

    cursor = await db.execute("SELECT COUNT(*) FROM steam_import_sessions")
    import_session_count = (await cursor.fetchone())[0]

    return {
        "base_url": settings.base_url,
        "library_dir": str(settings.library_dir),
        "auth_disabled": settings.disable_auth,
        "import_rate_limit_ms": settings.import_rate_limit_ms,
        "thumbnail_quality": settings.thumbnail_quality,
        "max_upload_size_mb": settings.max_upload_size_mb,
        "token_expiry_days": settings.token_expiry_days,
        "has_steam_api_key": bool(settings.steam_api_key),
        "has_steamgriddb_api_key": bool(settings.steamgriddb_api_key),
        "has_igdb_credentials": bool(settings.igdb_client_id and settings.igdb_client_secret),
        "library_size": format_file_size(lib_size),
        "library_size_bytes": lib_size,
        # Database stats
        "game_count": game_count,
        "screenshot_count": screenshot_count,
        "annotation_count": annotation_count,
        "active_share_count": active_share_count,
        "import_session_count": import_session_count,
    }


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}
