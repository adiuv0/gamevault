"""Timeline routes: screenshots grouped by date."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend.auth import require_auth
from backend.services.timeline_service import (
    get_timeline,
    get_timeline_insights,
    get_timeline_stats,
)

router = APIRouter(prefix="/api/timeline", tags=["timeline"], dependencies=[Depends(require_auth)])


@router.get("")
async def timeline(
    game_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1)] = 30,
):
    """Get screenshots grouped by date, paginated by days."""
    return await get_timeline(
        game_id=game_id,
        start_date=start_date,
        end_date=end_date,
        page=page,
        limit=min(limit, 100),
    )


@router.get("/stats")
async def timeline_stats():
    """Get timeline summary statistics."""
    return await get_timeline_stats()


@router.get("/insights")
async def timeline_insights(
    year: Annotated[int | None, Query(ge=1970, le=9999)] = None,
    game_id: int | None = None,
    today: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
):
    """Statistics for a year (or all time): streaks, sessions, top games, heatmap.

    ``today`` is the viewer's local date, so streaks and "on this day" follow their
    calendar rather than the server's.
    """
    return await get_timeline_insights(year=year, game_id=game_id, today=today)
