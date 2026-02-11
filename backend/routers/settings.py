"""Application settings routes."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth import require_auth
from backend.config import settings
from backend.database import get_db
from backend.services.filesystem import format_file_size, get_library_size_bytes

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_auth)])

# Allowed API key names that can be saved via the UI
_ALLOWED_KEYS = {
    "steam_api_key",
    "steamgriddb_api_key",
    "igdb_client_id",
    "igdb_client_secret",
}


async def get_effective_key(key_name: str) -> str:
    """Get an API key value: DB overrides env var."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT value FROM app_settings WHERE key = ?", (key_name,)
    )
    row = await cursor.fetchone()
    if row and row[0]:
        return row[0]
    return getattr(settings, key_name, "")


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

    # Check effective API key status (DB overrides env vars)
    has_steam = bool(await get_effective_key("steam_api_key"))
    has_griddb = bool(await get_effective_key("steamgriddb_api_key"))
    has_igdb = bool(
        await get_effective_key("igdb_client_id")
        and await get_effective_key("igdb_client_secret")
    )

    return {
        "base_url": settings.base_url,
        "library_dir": str(settings.library_dir),
        "auth_disabled": settings.disable_auth,
        "import_rate_limit_ms": settings.import_rate_limit_ms,
        "thumbnail_quality": settings.thumbnail_quality,
        "max_upload_size_mb": settings.max_upload_size_mb,
        "token_expiry_days": settings.token_expiry_days,
        "has_steam_api_key": has_steam,
        "has_steamgriddb_api_key": has_griddb,
        "has_igdb_credentials": has_igdb,
        "library_size": format_file_size(lib_size),
        "library_size_bytes": lib_size,
        # Database stats
        "game_count": game_count,
        "screenshot_count": screenshot_count,
        "annotation_count": annotation_count,
        "active_share_count": active_share_count,
        "import_session_count": import_session_count,
    }


class ApiKeyUpdate(BaseModel):
    key_name: str
    value: str


@router.put("/api-keys")
async def save_api_key(body: ApiKeyUpdate):
    """Save an API key to the database (overrides env var)."""
    if body.key_name not in _ALLOWED_KEYS:
        raise HTTPException(status_code=400, detail=f"Unknown key: {body.key_name}")
    if not body.value.strip():
        raise HTTPException(status_code=400, detail="Value cannot be empty")

    db = await get_db()
    await db.execute(
        """INSERT INTO app_settings (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (body.key_name, body.value.strip()),
    )
    await db.commit()
    return {"message": f"{body.key_name} saved"}


@router.delete("/api-keys/{key_name}")
async def delete_api_key(key_name: str):
    """Remove a saved API key from the database (falls back to env var)."""
    if key_name not in _ALLOWED_KEYS:
        raise HTTPException(status_code=400, detail=f"Unknown key: {key_name}")

    db = await get_db()
    await db.execute("DELETE FROM app_settings WHERE key = ?", (key_name,))
    await db.commit()
    return {"message": f"{key_name} removed"}


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}
