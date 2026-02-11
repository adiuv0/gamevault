"""Game metadata routes: fetch from Steam/SteamGridDB/IGDB, search external."""

from fastapi import APIRouter, Depends, HTTPException

from backend.auth import require_auth
from backend.services.metadata_service import (
    fetch_and_apply_metadata,
    search_external_games,
)

router = APIRouter(prefix="/api/metadata", tags=["metadata"], dependencies=[Depends(require_auth)])


@router.post("/fetch/{game_id}")
async def fetch_metadata(game_id: int):
    """Fetch and apply metadata for a game from all available sources."""
    try:
        result = await fetch_and_apply_metadata(game_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/search")
async def search_metadata(q: str = ""):
    """Search for game metadata from external sources."""
    if not q.strip():
        return {"results": []}
    results = await search_external_games(q.strip())
    return {"results": results}
