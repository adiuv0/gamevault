"""Game management routes."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from backend.auth import require_auth
from backend.config import settings
from backend.models.game import GameCreate, GameUpdate, GameResponse, GameListResponse
from backend.services import game_service
from backend.services.screenshot_service import list_screenshots

router = APIRouter(prefix="/api/games", tags=["games"], dependencies=[Depends(require_auth)])


@router.get("", response_model=GameListResponse)
async def list_games(sort: str = "name"):
    """List all games."""
    games = await game_service.list_games(sort=sort)
    return {"games": games, "total": len(games)}


@router.post("", response_model=GameResponse, status_code=201)
async def create_game(data: GameCreate):
    """Create a new game."""
    # Check for duplicate name
    existing = await game_service.get_game_by_name(data.name)
    if existing:
        raise HTTPException(status_code=409, detail="Game with this name already exists")

    game = await game_service.create_game(
        name=data.name,
        steam_app_id=data.steam_app_id,
    )
    return game


# ── Static path routes (must come BEFORE /{game_id} to avoid conflicts) ─────

@router.post("/cleanup-empty")
async def cleanup_empty_games():
    """Delete all games that have 0 screenshots.

    Useful for cleaning up placeholder games created by a failed sync.
    Returns the count and names of deleted games.
    """
    games = await game_service.list_games()
    deleted = []
    for game in games:
        if game.get("screenshot_count", 0) == 0:
            await game_service.delete_game(game["id"])
            deleted.append(game.get("name", f"Game {game['id']}"))

    return {"deleted_count": len(deleted), "deleted_games": deleted}


@router.get("/by-steam-appid/{app_id}")
async def get_or_create_by_steam_appid(app_id: int):
    """Get or create a game by its Steam app ID.

    Used by the CLI sync tool to resolve games before uploading.
    Tries to resolve the real game name via Steam Store API first.
    If the game already exists with a placeholder name ("App {id}"),
    re-fetches the real name from Steam.
    """
    existing = await game_service.get_game_by_steam_app_id(app_id)

    # If game exists but still has a placeholder name, try to resolve it
    if existing:
        current_name = existing.get("name", "")
        if current_name.startswith("App ") and current_name[4:].isdigit():
            try:
                from backend.services.metadata_service import fetch_steam_metadata
                steam_data = await fetch_steam_metadata(app_id)
                if steam_data and steam_data.get("name"):
                    await game_service.update_game(existing["id"], name=steam_data["name"])
                    existing = await game_service.get_game(existing["id"])
            except Exception:
                pass  # Keep placeholder name if resolution fails
        return existing

    # New game — try to get the real name from Steam Store API before creating
    name = f"App {app_id}"
    try:
        from backend.services.metadata_service import fetch_steam_metadata
        steam_data = await fetch_steam_metadata(app_id)
        if steam_data and steam_data.get("name"):
            name = steam_data["name"]
    except Exception:
        pass  # Fall back to generic name

    game = await game_service.get_or_create_game(
        name=name,
        steam_app_id=app_id,
    )
    return game


# ── Dynamic path routes (/{game_id}) ────────────────────────────────────────

@router.get("/{game_id}")
async def get_game(game_id: int):
    """Get game details."""
    game = await game_service.get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return game


@router.put("/{game_id}", response_model=GameResponse)
async def update_game(game_id: int, data: GameUpdate):
    """Update game metadata."""
    game = await game_service.update_game(
        game_id,
        name=data.name,
        steam_app_id=data.steam_app_id,
        developer=data.developer,
        publisher=data.publisher,
        release_date=data.release_date,
        genres=data.genres,
        description=data.description,
        is_public=data.is_public,
    )
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return game


@router.delete("/{game_id}")
async def delete_game(game_id: int):
    """Delete a game and all its screenshots from the database."""
    deleted = await game_service.delete_game(game_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Game not found")
    return {"message": "Game deleted"}


@router.get("/{game_id}/cover")
async def get_cover(game_id: int):
    """Serve game cover image."""
    game = await game_service.get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    if not game.get("cover_image_path"):
        raise HTTPException(status_code=404, detail="No cover image")

    cover_path = settings.library_dir / game["cover_image_path"]
    if not cover_path.exists():
        raise HTTPException(status_code=404, detail="Cover image file not found")

    return FileResponse(cover_path, media_type="image/jpeg")


@router.get("/{game_id}/screenshots")
async def get_game_screenshots(
    game_id: int,
    page: int = 1,
    limit: int = 50,
    sort: str = "date_desc",
):
    """List screenshots for a game."""
    game = await game_service.get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    screenshots, total = await list_screenshots(game_id, page, limit, sort)
    return {
        "screenshots": screenshots,
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": (page * limit) < total,
    }


@router.post("/{game_id}/refresh-metadata")
async def refresh_metadata(game_id: int):
    """Re-fetch game metadata from external sources."""
    from backend.services.metadata_service import fetch_and_apply_metadata

    game = await game_service.get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    result = await fetch_and_apply_metadata(game_id)
    return result
