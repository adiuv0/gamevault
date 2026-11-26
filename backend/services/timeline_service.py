"""Timeline service: groups captures by day and play session, and summarises a range.

Everything here counts logical captures: only the representative file of each capture
(``is_capture_primary = 1``) is read, so a Steam JPG and its Special K JXR count once.
Every query filters on the indexed day expression or the per-game moment index from
m015, so cost tracks the rows a request shows rather than the size of the library.
"""

import re
from collections import OrderedDict
from datetime import date, datetime, timedelta

from backend.config import settings
from backend.database import get_db
from backend.services.media_cache_service import media_version

# Must match idx_screenshots_primary_day exactly so day filters and ordering use it.
_DAY = "date(COALESCE(s.taken_at, s.uploaded_at))"
_MOMENT = "COALESCE(s.taken_at, s.uploaded_at)"
_FILE_HDR = "COALESCE({t}.is_hdr, lower(COALESCE({t}.format, '')) IN ('jxr', 'wdp'))"
# A capture is HDR when any of its files is. The representative is usually the SDR
# variant (m015 prefers it), so look at the group through idx_screenshots_game_capture_key
# unless the representative already answers it.
_IS_HDR = f"""CASE WHEN {_FILE_HDR.format(t="s")} THEN 1 ELSE EXISTS(
    SELECT 1 FROM screenshots h
    WHERE h.game_id = s.game_id
      AND COALESCE(h.capture_group_id, h.id) = COALESCE(s.capture_group_id, s.id)
      AND {_FILE_HDR.format(t="h")}
) END"""

# Captures of one game closer together than this belong to the same play session.
SESSION_GAP_MINUTES = 45
# Thumbnails sent per session, spread evenly from its first capture to its last.
SESSION_PREVIEWS = 12
TOP_GAMES = 5
ON_THIS_DAY_LIMIT = 8
HEATMAP_DAYS = 371  # 53 weeks: one full calendar row of weeks ending today.

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Insights read every capture in their range, so results are kept per
# (library revision, year, game, today). m021's triggers bump the revision on any
# write to captures, games or annotations, so a hit is never stale.
_INSIGHTS_CACHE: OrderedDict[tuple, dict] = OrderedDict()
_INSIGHTS_CACHE_SIZE = 32


def _filters(
    game_id: int | None, start_date: str | None, end_date: str | None
) -> tuple[list[str], list[object]]:
    conditions: list[str] = []
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
    return conditions, params


def _spread(items: list, count: int) -> list:
    """Pick ``count`` items evenly across ``items``, always keeping the first and last."""
    if len(items) <= count:
        return list(items)
    if count == 1:
        return [items[0]]
    step = (len(items) - 1) / (count - 1)
    return [items[round(i * step)] for i in range(count)]


def split_sessions(rows: list[dict], gap_minutes: int = SESSION_GAP_MINUTES) -> list[list[dict]]:
    """Group capture rows into play sessions.

    A session is a run of captures of one game where no two neighbours are more than
    ``gap_minutes`` apart. Rows need ``game_id`` and ``j`` (julian day). Sessions are
    returned newest first, each in capture order.
    """
    gap = gap_minutes / 1440
    by_game: dict[int, list[dict]] = {}
    for row in rows:
        by_game.setdefault(row["game_id"], []).append(row)
    sessions: list[list[dict]] = []
    for game_rows in by_game.values():
        game_rows.sort(key=lambda r: (r["j"], r["id"]))
        current = [game_rows[0]]
        for row in game_rows[1:]:
            if row["j"] - current[-1]["j"] > gap:
                sessions.append(current)
                current = [row]
            else:
                current.append(row)
        sessions.append(current)
    sessions.sort(key=lambda s: (s[-1]["j"], s[-1]["id"]), reverse=True)
    return sessions


def _minutes(first: float, last: float) -> int:
    return round((last - first) * 1440)


async def _capture_cards(db, ids: list[int]) -> dict[int, dict]:
    """Full card data for the given capture ids, read through the logical view."""
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    cursor = await db.execute(
        f"""SELECT lc.id, lc.filename, lc.file_path, lc.thumbnail_path_sm,
                   lc.thumbnail_path_md, lc.taken_at, lc.uploaded_at, lc.updated_at,
                   lc.capture_favorite AS is_favorite, lc.width, lc.height,
                   g.id AS game_id, g.name AS game_name,
                   lc.annotation_id IS NOT NULL AS has_annotation
            FROM logical_captures lc
            JOIN games g ON g.id = lc.game_id
            WHERE lc.id IN ({placeholders})""",
        ids,
    )
    columns = [desc[0] for desc in cursor.description]
    cards = {}
    for row in await cursor.fetchall():
        s = dict(zip(columns, row))
        cards[s["id"]] = {
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
        }
    return cards


async def get_timeline(
    game_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = 1,
    limit: int = 30,
) -> dict:
    """Get captures grouped by day and play session, paginated by days.

    Returns {days: [...], total_days, page, has_more}. Each day has its date, capture
    count, games, play sessions (newest first) and the preview captures those sessions
    show, in the same order.
    """
    db = await get_db()
    conditions, params = _filters(game_id, start_date, end_date)
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

    # One light row per capture on the visible days, straight off
    # idx_screenshots_primary_day. Sessions need every capture's time; the heavier
    # card data (favourites, annotations, paths) is read only for the previews.
    day_values = ", ".join("?" for _ in day_dates)
    filters = " AND ".join(["s.is_capture_primary = 1", *conditions, f"{_DAY} IN ({day_values})"])
    cursor = await db.execute(
        f"""SELECT s.id, s.game_id, {_DAY} AS day, {_MOMENT} AS moment,
                   julianday({_MOMENT}) AS j, {_IS_HDR} AS hdr
            FROM screenshots s
            WHERE {filters}""",
        [*params, *day_dates],
    )
    by_day: dict[str, list[dict]] = {day: [] for day in day_dates}
    for row in await cursor.fetchall():
        capture_id, gid, day, moment, j, hdr = row
        if j is None:
            continue
        by_day[day].append({"id": capture_id, "game_id": gid, "moment": moment, "j": j, "hdr": hdr})

    game_ids = {r["game_id"] for rows in by_day.values() for r in rows}
    games: dict[int, dict] = {}
    if game_ids:
        placeholders = ", ".join("?" for _ in game_ids)
        cursor = await db.execute(
            f"SELECT id, name, accent_color FROM games WHERE id IN ({placeholders})",
            list(game_ids),
        )
        games = {gid: {"name": name, "accent_color": accent} for gid, name, accent in await cursor.fetchall()}

    day_sessions: dict[str, list[tuple[list[dict], list[dict]]]] = {}
    preview_ids: list[int] = []
    for day, rows in by_day.items():
        pairs = []
        for session in split_sessions(rows) if rows else []:
            previews = _spread(session, SESSION_PREVIEWS)
            preview_ids.extend(r["id"] for r in previews)
            pairs.append((session, previews))
        day_sessions[day] = pairs
    cards = await _capture_cards(db, preview_ids)

    days = []
    for day in day_dates:
        sessions = []
        screenshots = []
        names: list[str] = []
        for session, previews in day_sessions[day]:
            game = games.get(session[0]["game_id"], {"name": "", "accent_color": None})
            if game["name"] not in names:
                names.append(game["name"])
            shown = [cards[r["id"]] for r in previews if r["id"] in cards]
            screenshots.extend(shown)
            sessions.append({
                "game_id": session[0]["game_id"],
                "game_name": game["name"],
                "accent_color": game["accent_color"],
                "started_at": session[0]["moment"],
                "ended_at": session[-1]["moment"],
                "duration_minutes": _minutes(session[0]["j"], session[-1]["j"]),
                "capture_count": len(session),
                "hdr_count": sum(1 for r in session if r["hdr"]),
                "screenshot_ids": [s["id"] for s in shown],
            })
        days.append({
            "date": day,
            "screenshot_count": len(by_day[day]),
            "games": names,
            "session_count": len(sessions),
            "session_minutes": sum(s["duration_minutes"] for s in sessions),
            "sessions": sessions,
            "screenshots": screenshots,
        })

    return {
        "days": days,
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


def _streaks(days: list[str]) -> tuple[dict | None, int]:
    """Longest run of consecutive days in ascending ``days`` and its length."""
    best: dict | None = None
    run_start = prev = None
    for value in days:
        current = date.fromisoformat(value)
        if prev is None or current - prev != timedelta(days=1):
            run_start = current
        length = (current - run_start).days + 1
        if best is None or length > best["days"]:
            best = {"days": length, "start": run_start.isoformat(), "end": current.isoformat()}
        prev = current
    return best, best["days"] if best else 0


async def _current_streak(db, game_id: int | None, today: date) -> dict:
    """Consecutive capture days ending today, or yesterday if today has none yet."""
    conditions, params = _filters(game_id, None, today.isoformat())
    where = " AND ".join(["s.is_capture_primary = 1", *conditions])
    cursor = await db.execute(
        f"SELECT DISTINCT {_DAY} AS day FROM screenshots s WHERE {where} ORDER BY day DESC",
        params,
    )
    expected = today
    length = 0
    end = None
    # Read day by day and stop at the first gap: cost is the streak, not the library.
    while True:
        row = await cursor.fetchone()
        if row is None or row[0] is None:
            break
        value = date.fromisoformat(row[0])
        if length == 0 and value == today - timedelta(days=1):
            expected = value
        if value != expected:
            break
        end = end or value
        length += 1
        expected = value - timedelta(days=1)
    await cursor.close()
    return {
        "days": length,
        "start": (end - timedelta(days=length - 1)).isoformat() if length else None,
        "end": end.isoformat() if end else None,
    }


async def get_timeline_insights(
    year: int | None = None,
    game_id: int | None = None,
    today: str | None = None,
) -> dict:
    """Statistics for one calendar year (or all time) on the Timeline page.

    ``today`` is the viewer's local date; streaks, the heatmap window and "on this day"
    are relative to it so the server's time zone does not shift them.
    """
    db = await get_db()
    cursor = await db.execute("SELECT token, value FROM library_revision WHERE id = 1")
    revision = tuple(await cursor.fetchone())
    # Without a valid viewer date, fall back to the server's local calendar day.
    today_date = datetime.now().astimezone().date()
    if today and _ISO_DAY.match(today):
        try:
            today_date = date.fromisoformat(today)
        except ValueError:
            pass
    key = (str(settings.db_path), revision, year, game_id, today_date)
    cached = _INSIGHTS_CACHE.get(key)
    if cached is not None:
        _INSIGHTS_CACHE.move_to_end(key)
        return cached
    result = await _compute_insights(db, year, game_id, today_date)
    _INSIGHTS_CACHE[key] = result
    while len(_INSIGHTS_CACHE) > _INSIGHTS_CACHE_SIZE:
        _INSIGHTS_CACHE.popitem(last=False)
    return result


async def _compute_insights(db, year: int | None, game_id: int | None, today_date: date) -> dict:
    start = f"{year:04d}-01-01" if year else None
    end = f"{year:04d}-12-31" if year else None
    conditions, params = _filters(game_id, start, end)
    where = " AND ".join(["s.is_capture_primary = 1", *conditions])

    cursor = await db.execute(
        f"""SELECT {_DAY} AS day, COUNT(*) AS captures, SUM({_IS_HDR}) AS hdr
            FROM screenshots s WHERE {where}
            GROUP BY day ORDER BY day""",
        params,
    )
    day_rows = [(d, c, h or 0) for d, c, h in await cursor.fetchall() if d]
    total = sum(c for _, c, _ in day_rows)
    hdr = sum(h for _, _, h in day_rows)
    best_streak, _ = _streaks([d for d, _, _ in day_rows])

    busiest = None
    if day_rows:
        busy_day, busy_count, _ = max(day_rows, key=lambda r: (r[1], r[0]))
        day_conditions, day_params = _filters(game_id, busy_day, busy_day)
        cursor = await db.execute(
            f"""SELECT g.id, g.name, COUNT(*) AS n FROM screenshots s
                JOIN games g ON g.id = s.game_id
                WHERE {" AND ".join(["s.is_capture_primary = 1", *day_conditions])}
                GROUP BY g.id ORDER BY n DESC, g.name LIMIT 1""",
            day_params,
        )
        top = await cursor.fetchone()
        busiest = {
            "date": busy_day,
            "captures": busy_count,
            "game_id": top[0] if top else None,
            "game_name": top[1] if top else None,
        }

    cursor = await db.execute(
        f"""SELECT g.id, g.name, g.accent_color, COUNT(*) AS n
            FROM screenshots s JOIN games g ON g.id = s.game_id
            WHERE {where}
            GROUP BY g.id ORDER BY n DESC, g.name""",
        params,
    )
    game_rows = await cursor.fetchall()
    top_games = [
        {"game_id": gid, "game_name": name, "accent_color": accent, "captures": n}
        for gid, name, accent, n in game_rows[:TOP_GAMES]
    ]
    other = game_rows[TOP_GAMES:]

    # Sessions over the whole range in SQL: mark a capture that starts a session
    # (first of its game, or more than the gap after the previous one), number the
    # sessions with a running sum, then measure each. Walks the per-game moment index.
    gap = SESSION_GAP_MINUTES / 1440
    cursor = await db.execute(
        f"""WITH ordered AS (
                SELECT s.game_id, julianday({_MOMENT}) AS j,
                       LAG(julianday({_MOMENT})) OVER (
                           PARTITION BY s.game_id ORDER BY {_MOMENT}, s.id
                       ) AS prev_j
                FROM screenshots s WHERE {where}
            ),
            numbered AS (
                SELECT game_id, j,
                       SUM(CASE WHEN prev_j IS NULL OR j - prev_j > ? THEN 1 ELSE 0 END)
                           OVER (PARTITION BY game_id ORDER BY j ROWS UNBOUNDED PRECEDING) AS n
                FROM ordered WHERE j IS NOT NULL
            ),
            sessions AS MATERIALIZED (
                SELECT game_id, MIN(j) AS first_j, MAX(j) AS last_j, COUNT(*) AS captures
                FROM numbered GROUP BY game_id, n
            )
            SELECT (SELECT COUNT(*) FROM sessions),
                   s.game_id, g.name, s.first_j, s.last_j, s.captures,
                   date(s.first_j)
            FROM sessions s JOIN games g ON g.id = s.game_id
            ORDER BY s.last_j - s.first_j DESC, s.captures DESC
            LIMIT 1""",
        [*params, gap],
    )
    longest_row = await cursor.fetchone()
    session_count = longest_row[0] if longest_row else 0
    longest = None
    if longest_row:
        _, gid, name, first_j, last_j, captures, first_day = longest_row
        longest = {
            "game_id": gid,
            "game_name": name,
            "date": first_day,
            "duration_minutes": _minutes(first_j, last_j),
            "captures": captures,
        }

    # The hour as the capture's clock wrote it (characters 12-13 of the stored
    # time). strftime would convert times stored with an offset to UTC.
    cursor = await db.execute(
        f"""SELECT CAST(substr({_MOMENT}, 12, 2) AS INTEGER) AS hour, COUNT(*)
            FROM screenshots s WHERE {where} GROUP BY hour""",
        params,
    )
    hours = [0] * 24
    for hour, count in await cursor.fetchall():
        if hour is not None and 0 <= hour < 24:
            hours[hour] += count

    cursor = await db.execute(
        f"""SELECT MIN({_DAY}), MAX({_DAY}) FROM screenshots s
            WHERE {" AND ".join(["s.is_capture_primary = 1", *_filters(game_id, None, None)[0]])}""",
        _filters(game_id, None, None)[1],
    )
    first_day, last_day = await cursor.fetchone()
    years = (
        list(range(int(last_day[:4]), int(first_day[:4]) - 1, -1))
        if first_day and last_day
        else []
    )

    if year:
        heat_start, heat_end = date(year, 1, 1), date(year, 12, 31)
    else:
        heat_end = today_date
        heat_start = heat_end - timedelta(days=HEATMAP_DAYS - 1)
    if year:
        heatmap = [[d, c] for d, c, _ in day_rows]
    else:
        lo, hi = heat_start.isoformat(), heat_end.isoformat()
        heatmap = [[d, c] for d, c, _ in day_rows if lo <= d <= hi]

    return {
        "year": year,
        "years": years,
        "range": {
            "start": day_rows[0][0] if day_rows else None,
            "end": day_rows[-1][0] if day_rows else None,
        },
        "total_captures": total,
        "active_days": len(day_rows),
        "games": len(game_rows),
        "hdr_captures": hdr,
        "busiest_day": busiest,
        "best_streak": best_streak,
        "current_streak": await _current_streak(db, game_id, today_date),
        "session_count": session_count,
        "longest_session": longest,
        "top_games": top_games,
        "hours": hours,
        "other_games": {"games": len(other), "captures": sum(r[3] for r in other)},
        "heatmap": {"start": heat_start.isoformat(), "end": heat_end.isoformat(), "days": heatmap},
        "on_this_day": await _on_this_day(db, game_id, today_date, first_day),
    }


async def _on_this_day(db, game_id: int | None, today: date, first_day: str | None) -> dict:
    """Captures from today's month and day in earlier years, a few per year."""
    if not first_day:
        return {"date": today.isoformat(), "captures": []}
    dates = []
    for y in range(today.year - 1, int(first_day[:4]) - 1, -1):
        try:
            dates.append(today.replace(year=y).isoformat())
        except ValueError:  # Feb 29 in a non-leap year
            continue
    if not dates:
        return {"date": today.isoformat(), "captures": []}
    conditions, params = _filters(game_id, None, None)
    placeholders = ", ".join("?" for _ in dates)
    where = " AND ".join(["s.is_capture_primary = 1", *conditions, f"{_DAY} IN ({placeholders})"])
    cursor = await db.execute(
        f"""SELECT s.id, {_DAY} AS day FROM screenshots s WHERE {where}
            ORDER BY day DESC, {_MOMENT}, s.id""",
        [*params, *dates],
    )
    per_year: dict[str, list[int]] = {}
    for capture_id, day in await cursor.fetchall():
        per_year.setdefault(day, []).append(capture_id)
    # Round-robin across years so one big day does not crowd out the others, and
    # spread within each day so the picks are not one burst of near-identical shots.
    pools = [_spread(ids, ON_THIS_DAY_LIMIT) for ids in per_year.values()]
    picked: list[int] = []
    while len(picked) < ON_THIS_DAY_LIMIT and any(pools):
        for pool in pools:
            if pool and len(picked) < ON_THIS_DAY_LIMIT:
                picked.append(pool.pop(0))
    cards = await _capture_cards(db, picked)
    return {"date": today.isoformat(), "captures": [cards[i] for i in picked if i in cards]}
