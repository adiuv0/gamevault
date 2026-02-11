"""Search routes with FTS5 full-text search."""

from fastapi import APIRouter, Depends

from backend.auth import require_auth
from backend.services.search_service import search_screenshots

router = APIRouter(prefix="/api/search", tags=["search"], dependencies=[Depends(require_auth)])


@router.get("")
async def search(
    q: str = "",
    game_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    favorites_only: bool = False,
    sort: str = "relevance",
    page: int = 1,
    limit: int = 50,
):
    """Full-text search across screenshots and annotations.

    - q: search query (uses FTS5 with BM25 ranking)
    - game_id: filter by game
    - date_from/date_to: date range filter (ISO format)
    - favorites_only: only show favorites
    - sort: relevance | date_desc | date_asc
    - page/limit: pagination
    """
    if limit > 100:
        limit = 100

    results, total = await search_screenshots(
        query=q,
        game_id=game_id,
        date_from=date_from,
        date_to=date_to,
        favorites_only=favorites_only,
        sort=sort,
        page=page,
        limit=limit,
    )

    return {
        "results": results,
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": (page * limit) < total,
    }
