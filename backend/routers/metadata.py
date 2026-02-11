"""Game metadata routes: fetch from Steam/SteamGridDB/IGDB, search external."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from backend.auth import require_auth
from backend.services.metadata_service import (
    fetch_and_apply_metadata,
    search_external_games,
)
from backend.services.game_service import list_games

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/metadata", tags=["metadata"], dependencies=[Depends(require_auth)])


@router.post("/fetch/{game_id}")
async def fetch_metadata(game_id: int):
    """Fetch and apply metadata for a game from all available sources."""
    try:
        result = await fetch_and_apply_metadata(game_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/fetch-all")
async def fetch_all_metadata():
    """Fetch metadata for every game that is missing name/cover/description.

    Skips games that already have all core metadata populated.
    Returns a summary of what was updated.
    """
    games = await list_games()
    results = []
    updated = 0
    skipped = 0
    errors = 0

    for game in games:
        # Skip games that already look complete
        has_cover = bool(game.get("cover_image_path"))
        has_desc = bool(game.get("description"))
        is_placeholder = (game.get("name") or "").startswith("App ")
        needs_work = is_placeholder or not has_cover or not has_desc

        if not needs_work:
            skipped += 1
            continue

        try:
            result = await fetch_and_apply_metadata(game["id"])
            if result.get("fields_updated") or result.get("cover_downloaded"):
                updated += 1
            results.append(result)
        except Exception as e:
            errors += 1
            logger.warning("Metadata fetch failed for game %d: %s", game["id"], e)

        # Rate-limit: avoid Steam Store API throttling (~300ms between games)
        await asyncio.sleep(0.3)

    return {
        "total_games": len(games),
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "details": results,
    }


@router.get("/search")
async def search_metadata(q: str = ""):
    """Search for game metadata from external sources."""
    if not q.strip():
        return {"results": []}
    results = await search_external_games(q.strip())
    return {"results": results}
