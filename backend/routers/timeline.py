"""Timeline routes: screenshots grouped by date."""

from fastapi import APIRouter, Depends

from backend.auth import require_auth
from backend.services.timeline_service import get_timeline, get_timeline_stats

router = APIRouter(prefix="/api/timeline", tags=["timeline"], dependencies=[Depends(require_auth)])


@router.get("")
async def timeline(
    game_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = 1,
    limit: int = 30,
):
    """Get screenshots grouped by date, paginated by days."""
    return await get_timeline(
        game_id=game_id,
        start_date=start_date,
        end_date=end_date,
        page=page,
        limit=limit,
    )


@router.get("/stats")
async def timeline_stats():
    """Get timeline summary statistics."""
    return await get_timeline_stats()
