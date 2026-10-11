"""Timeline service: groups screenshots by date for calendar/timeline views."""

from backend.database import get_db
from backend.services.media_cache_service import media_version

# Must match idx_screenshots_primary_day exactly so day filters and ordering use it.
_DAY = "date(COALESCE(s.taken_at, s.uploaded_at))"
_SCREENSHOTS_PER_DAY = 50


async def get_timeline(
    game_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = 1,
    limit: int = 30,
) -> dict:
    """Get screenshots grouped by date, paginated by days.

    Returns {days: [...], total_days, page, has_more} where each day has
    date, screenshot_count, games list, and screenshot previews.
    """
    db = await get_db()

    conditions = []
    params: list[object] = []
    if game_id:
        conditions.append("s.game_id = ?")
        params.append(game_id)
    if start_date:
        conditions.append(f"{_DAY} >= ?")
        params.append(start_date)
    if end_date:
        conditions.append(f"{_DAY} <= ?")
        params.append(end_date)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    cursor = await db.execute(
        f"SELECT COUNT(DISTINCT {_DAY}) FROM logical_captures s {where}",
        params,
    )
    total_days = (await cursor.fetchone())[0]

    offset = (page - 1) * limit
    cursor = await db.execute(
        f"""SELECT DISTINCT {_DAY} AS day
            FROM logical_captures s
            {where}
            ORDER BY day DESC
            LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    )
    day_dates = [row[0] for row in await cursor.fetchall() if row[0]]
    if not day_dates:
        return {"days": [], "total_days": total_days, "page": page, "has_more": False}

    # One query for every visible day instead of two per day. Each day's newest
    # captures come straight off idx_screenshots_primary_day (day, moment, id), so
    # a day with thousands of captures still reads only the rows it shows; the
    # view's favourite and annotation lookups run for those rows alone.
    filters = " AND ".join(["s.is_capture_primary = 1", *conditions, f"{_DAY} = d.day"])
    day_values = ", ".join("(?)" for _ in day_dates)
    cursor = await db.execute(
        f"""WITH visible(day) AS (VALUES {day_values}),
            counts AS MATERIALIZED (
                SELECT d.day, (SELECT COUNT(*) FROM screenshots s WHERE {filters}) AS day_count
                FROM visible d
            ),
            picked AS MATERIALIZED (
                SELECT d.day, p.id
                FROM visible d
                JOIN screenshots p ON p.id IN (
                    SELECT s.id FROM screenshots s
                    WHERE {filters}
                    ORDER BY COALESCE(s.taken_at, s.uploaded_at) DESC, s.id DESC
                    LIMIT ?
                )
            )
            SELECT lc.id, lc.filename, lc.file_path, lc.thumbnail_path_sm,
                   lc.thumbnail_path_md, lc.taken_at, lc.uploaded_at, lc.updated_at,
                   lc.capture_favorite AS is_favorite, lc.width, lc.height,
                   g.id AS game_id, g.name AS game_name,
                   lc.annotation_id IS NOT NULL AS has_annotation,
                   r.day, c.day_count
            FROM picked r
            JOIN counts c ON c.day = r.day
            JOIN logical_captures lc ON lc.id = r.id
            JOIN games g ON g.id = lc.game_id
            ORDER BY r.day DESC, COALESCE(lc.taken_at, lc.uploaded_at) DESC, lc.id DESC""",
        [*day_dates, *params, *params, _SCREENSHOTS_PER_DAY],
    )
    columns = [desc[0] for desc in cursor.description]
    by_day: dict[str, dict] = {
        day: {"date": day, "screenshot_count": 0, "games": [], "screenshots": []}
        for day in day_dates
    }
    for row in await cursor.fetchall():
        s = dict(zip(columns, row))
        day = by_day[s["day"]]
        day["screenshot_count"] = s["day_count"]
        if s["game_name"] not in day["games"]:
            day["games"].append(s["game_name"])
        day["screenshots"].append({
            "id": s["id"],
            "filename": s["filename"],
            "file_path": s["file_path"],
            "thumbnail_path_sm": s["thumbnail_path_sm"],
            "thumbnail_path_md": s["thumbnail_path_md"],
            "taken_at": s["taken_at"],
            "uploaded_at": s["uploaded_at"],
            "is_favorite": bool(s["is_favorite"]),
            "width": s["width"],
            "height": s["height"],
            "game_id": s["game_id"],
            "game_name": s["game_name"],
            "has_annotation": bool(s["has_annotation"]),
            "media_version": media_version(s["updated_at"]),
        })

    return {
        "days": [by_day[day] for day in day_dates],
        "total_days": total_days,
        "page": page,
        "has_more": (offset + limit) < total_days,
    }


async def get_timeline_stats() -> dict:
    """Get summary statistics for the timeline."""
    db = await get_db()

    cursor = await db.execute(f"""
        SELECT
            COUNT(*) as total_screenshots,
            COUNT(DISTINCT {_DAY}) as total_days,
            MIN({_DAY}) as earliest_date,
            MAX({_DAY}) as latest_date,
            COUNT(DISTINCT game_id) as total_games
        FROM logical_captures s
    """)
    row = await cursor.fetchone()
    columns = [desc[0] for desc in cursor.description]
    return dict(zip(columns, row))
