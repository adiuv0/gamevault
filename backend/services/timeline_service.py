"""Timeline service: groups screenshots by date for calendar/timeline views."""

from backend.database import get_db


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

    # Build WHERE clause
    conditions = []
    params = []

    if game_id:
        conditions.append("s.game_id = ?")
        params.append(game_id)
    if start_date:
        conditions.append("date(COALESCE(s.taken_at, s.uploaded_at)) >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("date(COALESCE(s.taken_at, s.uploaded_at)) <= ?")
        params.append(end_date)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # Get total number of distinct days
    cursor = await db.execute(
        f"""SELECT COUNT(DISTINCT date(COALESCE(s.taken_at, s.uploaded_at)))
            FROM screenshots s
            {where}""",
        params,
    )
    total_days = (await cursor.fetchone())[0]

    # Get the distinct days for this page, newest first
    offset = (page - 1) * limit
    cursor = await db.execute(
        f"""SELECT DISTINCT date(COALESCE(s.taken_at, s.uploaded_at)) as day
            FROM screenshots s
            {where}
            ORDER BY day DESC
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    )
    day_rows = await cursor.fetchall()
    day_dates = [row[0] for row in day_rows if row[0]]

    if not day_dates:
        return {"days": [], "total_days": total_days, "page": page, "has_more": False}

    # For each day, get screenshot details
    days = []
    for day_date in day_dates:
        day_conditions = list(conditions) + ["date(COALESCE(s.taken_at, s.uploaded_at)) = ?"]
        day_params = list(params) + [day_date]
        day_where = f"WHERE {' AND '.join(day_conditions)}"

        cursor = await db.execute(
            f"""SELECT s.id, s.filename, s.file_path, s.thumbnail_path_sm,
                       s.thumbnail_path_md, s.taken_at, s.uploaded_at,
                       s.is_favorite, s.width, s.height,
                       g.id as game_id, g.name as game_name,
                       EXISTS(SELECT 1 FROM annotations a WHERE a.screenshot_id = s.id) as has_annotation
                FROM screenshots s
                JOIN games g ON g.id = s.game_id
                {day_where}
                ORDER BY COALESCE(s.taken_at, s.uploaded_at) DESC, s.id DESC
                LIMIT 50""",
            day_params,
        )
        columns = [desc[0] for desc in cursor.description]
        rows = await cursor.fetchall()
        screenshots = [dict(zip(columns, row)) for row in rows]

        # Count total for this day
        cursor = await db.execute(
            f"""SELECT COUNT(*) FROM screenshots s {day_where}""",
            day_params,
        )
        day_count = (await cursor.fetchone())[0]

        # Unique games for this day
        game_names = list({s["game_name"] for s in screenshots})

        days.append({
            "date": day_date,
            "screenshot_count": day_count,
            "games": game_names,
            "screenshots": [
                {
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
                }
                for s in screenshots
            ],
        })

    has_more = (offset + limit) < total_days

    return {
        "days": days,
        "total_days": total_days,
        "page": page,
        "has_more": has_more,
    }


async def get_timeline_stats() -> dict:
    """Get summary statistics for the timeline."""
    db = await get_db()

    cursor = await db.execute("""
        SELECT
            COUNT(*) as total_screenshots,
            COUNT(DISTINCT date(COALESCE(taken_at, uploaded_at))) as total_days,
            MIN(date(COALESCE(taken_at, uploaded_at))) as earliest_date,
            MAX(date(COALESCE(taken_at, uploaded_at))) as latest_date,
            COUNT(DISTINCT game_id) as total_games
        FROM screenshots
    """)
    row = await cursor.fetchone()
    columns = [desc[0] for desc in cursor.description]
    return dict(zip(columns, row))
