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


@router.get("/{game_id}")
async def get_game(game_id: int):
    """Get game details."""
    game = await game_service.get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return game


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


@router.get("/by-steam-appid/{app_id}")
async def get_or_create_by_steam_appid(app_id: int):
    """Get or create a game by its Steam app ID.

    Used by the CLI sync tool to resolve games before uploading.
    """
    game = await game_service.get_or_create_game(
        name=f"App {app_id}",
        steam_app_id=app_id,
    )
    return game


@router.post("/{game_id}/refresh-metadata")
async def refresh_metadata(game_id: int):
    """Re-fetch game metadata from external sources."""
    game = await game_service.get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    # TODO: Phase 5 - metadata fetcher integration
    return {"message": "Metadata refresh will be available in a future update"}
