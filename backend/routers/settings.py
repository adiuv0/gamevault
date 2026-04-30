"""Application settings routes."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.auth import require_auth
from backend.config import settings
from backend.database import get_db
from backend.services.filesystem import format_file_size, get_library_size_bytes

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_auth)])

# Keys that hold sensitive values — listed via has_<x> flags only.
_SECRET_KEYS = {
    "steam_api_key",
    "steamgriddb_api_key",
    "igdb_client_id",
    "igdb_client_secret",
}

# Plain user-preference keys — value is returned as-is.
_PREFERENCE_KEYS = {
    "specialk_path",
    "tone_map_algorithm",
    "tone_map_exposure",
}

_VALID_TONE_MAP_ALGORITHMS = {"reinhard", "aces", "clip"}


# Backwards-compat alias for any old code paths.
_ALLOWED_KEYS = _SECRET_KEYS | _PREFERENCE_KEYS


async def get_effective_key(key_name: str) -> str:
    """Get a key value: DB overrides env var (env var only used for secrets)."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT value FROM app_settings WHERE key = ?", (key_name,)
    )
    row = await cursor.fetchone()
    if row and row[0]:
        return row[0]
    return getattr(settings, key_name, "")


async def _read_preference(key_name: str, default: str = "") -> str:
    """Get a preference value from the DB (no env-var fallback)."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT value FROM app_settings WHERE key = ?", (key_name,)
    )
    row = await cursor.fetchone()
    if row and row[0]:
        return row[0]
    return default


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

    # User preferences
    specialk_path = await _read_preference("specialk_path", "")
    tone_map_algorithm = await _read_preference("tone_map_algorithm", "reinhard")
    tone_map_exposure_raw = await _read_preference("tone_map_exposure", "1.0")
    try:
        tone_map_exposure = float(tone_map_exposure_raw)
    except ValueError:
        tone_map_exposure = 1.0

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
        # Special K + HDR preferences
        "specialk_path": specialk_path,
        "tone_map_algorithm": tone_map_algorithm,
        "tone_map_exposure": tone_map_exposure,
    }


class ApiKeyUpdate(BaseModel):
    key_name: str
    value: str


@router.put("/api-keys")
async def save_api_key(body: ApiKeyUpdate):
    """Save an API key to the database (overrides env var)."""
    if body.key_name not in _SECRET_KEYS:
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
    if key_name not in _SECRET_KEYS:
        raise HTTPException(status_code=400, detail=f"Unknown key: {key_name}")

    db = await get_db()
    await db.execute("DELETE FROM app_settings WHERE key = ?", (key_name,))
    await db.commit()
    return {"message": f"{key_name} removed"}


# ── User preference endpoints ────────────────────────────────────────────────


class PreferenceUpdate(BaseModel):
    specialk_path: str | None = None
    tone_map_algorithm: str | None = None
    tone_map_exposure: float | None = Field(default=None, ge=0.05, le=8.0)


@router.put("/preferences")
async def save_preferences(body: PreferenceUpdate):
    """Save Special K + HDR tone-map preferences."""
    updates: list[tuple[str, str]] = []

    if body.specialk_path is not None:
        # Allow clearing the path with an empty string.
        updates.append(("specialk_path", body.specialk_path.strip()))

    if body.tone_map_algorithm is not None:
        if body.tone_map_algorithm not in _VALID_TONE_MAP_ALGORITHMS:
            raise HTTPException(
                status_code=400,
                detail=f"tone_map_algorithm must be one of {sorted(_VALID_TONE_MAP_ALGORITHMS)}",
            )
        updates.append(("tone_map_algorithm", body.tone_map_algorithm))

    if body.tone_map_exposure is not None:
        updates.append(("tone_map_exposure", f"{body.tone_map_exposure:.3f}"))

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    db = await get_db()
    tone_map_changed = False
    for key, value in updates:
        if value == "":
            await db.execute("DELETE FROM app_settings WHERE key = ?", (key,))
        else:
            await db.execute(
                """INSERT INTO app_settings (key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (key, value),
            )
        if key in ("tone_map_algorithm", "tone_map_exposure"):
            tone_map_changed = True
    await db.commit()

    # Invalidate the in-memory tone-map cache so the very next thumbnail
    # generated picks up the new algorithm/exposure. Cheap (just clears a
    # module-level dict), cleaner than waiting for process restart.
    if tone_map_changed:
        from backend.services.image_processor import invalidate_tone_map_cache
        invalidate_tone_map_cache()

    return {"message": "Preferences saved"}


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}
