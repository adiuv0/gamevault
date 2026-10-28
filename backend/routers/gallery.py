"""Public read-only gallery routes (no authentication required).

Response shapes for these routes are *intentionally slimmer* than the
admin endpoints (see ``models/screenshot.py:PublicScreenshot`` and
``models/game.py:PublicGame``). The unauthenticated public must not see
internal fields like ``file_path``, ``sha256_hash``, ``exif_data``,
``steam_screenshot_id``, or ``source`` — that's GV-002 from the audit.
FastAPI's ``response_model`` enforces this by dropping any field not
declared on the model when serializing.
"""

import mimetypes

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from backend.database import get_db
from backend.models.game import PublicGame, PublicGameListResponse
from backend.models.screenshot import PublicScreenshot, PublicScreenshotListResponse
from backend.services import full_render_service, game_service, screenshot_service
from backend.services.filesystem import safe_library_path, safe_media_path
from backend.services.media_cache_service import media_response_headers, media_version

router = APIRouter(prefix="/api/gallery", tags=["gallery"])

# Python's mimetypes module doesn't know JPEG XR.
_EXTRA_MIME_TYPES = {
    ".jxr": "image/vnd.ms-photo",
    ".wdp": "image/vnd.ms-photo",
}


# ── Helpers ─────────────────────────────────────────────────────────────────


async def _get_public_game(game_id: int) -> dict:
    """Get a game by ID, raising 404 if it doesn't exist or isn't public."""
    game = await game_service.get_game(game_id)
    if not game or not game.get("is_public"):
        raise HTTPException(status_code=404, detail="Game not found")
    return game


async def _get_public_screenshot(screenshot_id: int) -> dict:
    """Get a screenshot, raising 404 if its game isn't public."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT s.id, s.file_path, s.thumbnail_path_sm, s.thumbnail_path_md,
                  s.preview_path, s.format, s.updated_at, g.is_public, g.folder_name
           FROM screenshots s
           JOIN games g ON g.id = s.game_id
           WHERE s.id = ? AND g.is_public = 1""",
        (screenshot_id,),
    )
    columns = [desc[0] for desc in cursor.description]
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return dict(zip(columns, row))


# ── Game endpoints ──────────────────────────────────────────────────────────


@router.get("/games", response_model=PublicGameListResponse)
async def gallery_list_games(sort: str = "name"):
    """List all public games with slim response."""
    games = await game_service.list_public_games(sort=sort)
    return {"games": games, "total": len(games)}


@router.get("/games/{game_id}", response_model=PublicGame)
async def gallery_get_game(game_id: int):
    """Get a public game's details (slim shape)."""
    return await _get_public_game(game_id)


@router.get("/games/{game_id}/screenshots", response_model=PublicScreenshotListResponse)
async def gallery_game_screenshots(
    game_id: int,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    sort: str = "date_desc",
    cursor: str | None = None,
):
    """List screenshots for a public game with slim response shape."""
    await _get_public_game(game_id)
    using_cursor = cursor is not None or page == 1
    next_cursor = None
    if using_cursor:
        try:
            screenshots, total, next_cursor = (
                await screenshot_service.list_screenshots_cursor(
                    game_id, cursor or None, limit, sort
                )
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    else:
        screenshots, total = await screenshot_service.list_screenshots(
            game_id, page, limit, sort
        )
    return {
        "screenshots": screenshots,
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": next_cursor is not None if using_cursor else (page * limit) < total,
        "next_cursor": next_cursor,
    }


@router.get("/games/{game_id}/cover")
async def gallery_cover(game_id: int):
    """Serve cover image for a public game."""
    game = await _get_public_game(game_id)

    if not game.get("cover_image_path"):
        raise HTTPException(status_code=404, detail="No cover image")

    cover_path = safe_library_path(game["cover_image_path"])
    if not cover_path.exists():
        raise HTTPException(status_code=404, detail="Cover image file not found")

    return FileResponse(
        cover_path,
        media_type="image/jpeg",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
            "Cache-Control": "public, max-age=86400",
        },
    )


# ── Screenshot endpoints ────────────────────────────────────────────────────


@router.get("/screenshots/{screenshot_id}", response_model=PublicScreenshot)
async def gallery_get_screenshot(screenshot_id: int):
    """Get public logical-capture details for the viewer."""
    await _get_public_screenshot(screenshot_id)
    screenshot = await screenshot_service.get_capture_details(screenshot_id)
    if not screenshot:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return screenshot


@router.get("/screenshots/{screenshot_id}/image")
async def gallery_screenshot_image(screenshot_id: int, v: str | None = None):
    """Serve full-resolution image if its game is public."""
    screenshot = await _get_public_screenshot(screenshot_id)

    file_path = safe_library_path(screenshot.get("file_path"))
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Image file not found")

    media_type = (
        _EXTRA_MIME_TYPES.get(file_path.suffix.lower())
        or mimetypes.guess_type(str(file_path))[0]
        or "image/jpeg"
    )
    return FileResponse(
        file_path,
        media_type=media_type,
        headers=media_response_headers(
            public=True,
            requested_version=v,
            current_version=media_version(screenshot["updated_at"]),
        ),
    )


@router.get("/screenshots/{screenshot_id}/full")
async def gallery_screenshot_full(screenshot_id: int, v: str | None = None):
    """Serve the full-resolution image in a browser format if its game is public."""
    screenshot = await _get_public_screenshot(screenshot_id)
    if not full_render_service.needs_full_render(screenshot):
        return await gallery_screenshot_image(screenshot_id, v)

    render = await full_render_service.ensure_full_render(screenshot)
    if render is None:
        return await gallery_screenshot_preview(screenshot_id, v)
    return FileResponse(
        render,
        media_type="image/jpeg",
        headers=media_response_headers(
            public=True,
            requested_version=v,
            current_version=media_version(screenshot["updated_at"]),
        ),
    )


@router.get("/screenshots/{screenshot_id}/thumb/{size}")
async def gallery_screenshot_thumb(screenshot_id: int, size: str, v: str | None = None):
    """Serve thumbnail if its game is public."""
    if size not in ("sm", "md"):
        raise HTTPException(status_code=400, detail="Size must be 'sm' or 'md'")

    screenshot = await _get_public_screenshot(screenshot_id)

    path_field = "thumbnail_path_sm" if size == "sm" else "thumbnail_path_md"
    rel_path = screenshot.get(path_field) or screenshot.get("file_path")

    full_path = safe_media_path(rel_path)
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    return FileResponse(
        full_path,
        media_type="image/jpeg",
        headers=media_response_headers(
            public=True,
            requested_version=v,
            current_version=media_version(screenshot["updated_at"]),
        ),
    )


@router.get("/screenshots/{screenshot_id}/preview")
async def gallery_screenshot_preview(screenshot_id: int, v: str | None = None):
    """Serve a bounded public preview, falling back while backfill runs."""
    screenshot = await _get_public_screenshot(screenshot_id)
    rel_path = None
    full_path = None
    for candidate in (
        screenshot.get("preview_path"),
        screenshot.get("thumbnail_path_md"),
        screenshot.get("file_path"),
    ):
        if not candidate:
            continue
        try:
            resolved = safe_media_path(candidate)
        except HTTPException:
            continue
        if resolved.exists():
            rel_path = candidate
            full_path = resolved
            break
    if rel_path is None or full_path is None:
        raise HTTPException(status_code=404, detail="Preview not found")

    is_derivative = rel_path != screenshot.get("file_path")
    media_type = (
        "image/jpeg"
        if is_derivative
        else _EXTRA_MIME_TYPES.get(full_path.suffix.lower())
        or mimetypes.guess_type(str(full_path))[0]
        or "image/jpeg"
    )
    return FileResponse(
        full_path,
        media_type=media_type,
        headers=media_response_headers(
            public=True,
            requested_version=v,
            current_version=media_version(screenshot["updated_at"]),
        ),
    )
