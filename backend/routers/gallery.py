"""Public read-only gallery routes (no authentication required)."""

import mimetypes

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.config import settings
from backend.database import get_db
from backend.services import game_service
from backend.services.screenshot_service import list_screenshots

router = APIRouter(prefix="/api/gallery", tags=["gallery"])


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
        """SELECT s.*, g.is_public, g.folder_name
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


@router.get("/games")
async def gallery_list_games(sort: str = "name"):
    """List all public games."""
    games = await game_service.list_public_games(sort=sort)
    return {"games": games, "total": len(games)}


@router.get("/games/{game_id}")
async def gallery_get_game(game_id: int):
    """Get a public game's details."""
    return await _get_public_game(game_id)


@router.get("/games/{game_id}/screenshots")
async def gallery_game_screenshots(
    game_id: int,
    page: int = 1,
    limit: int = 50,
    sort: str = "date_desc",
):
    """List screenshots for a public game."""
    await _get_public_game(game_id)
    screenshots, total = await list_screenshots(game_id, page, limit, sort)
    return {
        "screenshots": screenshots,
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": (page * limit) < total,
    }


@router.get("/games/{game_id}/cover")
async def gallery_cover(game_id: int):
    """Serve cover image for a public game."""
    game = await _get_public_game(game_id)

    if not game.get("cover_image_path"):
        raise HTTPException(status_code=404, detail="No cover image")

    cover_path = settings.library_dir / game["cover_image_path"]
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


@router.get("/screenshots/{screenshot_id}/image")
async def gallery_screenshot_image(screenshot_id: int):
    """Serve full-resolution image if its game is public."""
    screenshot = await _get_public_screenshot(screenshot_id)

    file_path = settings.library_dir / screenshot["file_path"]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Image file not found")

    media_type = mimetypes.guess_type(str(file_path))[0] or "image/jpeg"
    return FileResponse(
        file_path,
        media_type=media_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
            "Cache-Control": "public, max-age=86400",
        },
    )


@router.get("/screenshots/{screenshot_id}/thumb/{size}")
async def gallery_screenshot_thumb(screenshot_id: int, size: str):
    """Serve thumbnail if its game is public."""
    if size not in ("sm", "md"):
        raise HTTPException(status_code=400, detail="Size must be 'sm' or 'md'")

    screenshot = await _get_public_screenshot(screenshot_id)

    path_field = "thumbnail_path_sm" if size == "sm" else "thumbnail_path_md"
    rel_path = screenshot.get(path_field) or screenshot["file_path"]

    full_path = settings.library_dir / rel_path
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    return FileResponse(
        full_path,
        media_type="image/jpeg",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
            "Cache-Control": "public, max-age=86400",
        },
    )
