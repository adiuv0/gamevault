"""Game management service: CRUD operations and folder management."""

import logging

from backend.config import settings
from backend.database import get_db
from backend.services.filesystem import (
    ensure_game_directories,
    get_game_dir,
    sanitize_folder_name,
)
from backend.services.performance_diagnostics import measure_async

logger = logging.getLogger(__name__)


# GV-013: explicit allowlist for ORDER BY fragments. Any caller-supplied
# sort token is translated through this map; an unknown token falls back
# to GAME_DEFAULT_SORT instead of becoming part of a SQL string.
GAME_SORT_CLAUSES: dict[str, str] = {
    "name": "g.name ASC",
    "date": "g.last_screenshot_date DESC NULLS LAST",
    "count": "g.screenshot_count DESC",
}
GAME_DEFAULT_SORT = "g.name ASC"


@measure_async("database", "game_list_private", threshold_ms=250)
async def list_games(sort: str = "name") -> list[dict]:
    """List all games with sorting."""
    db = await get_db()

    order_clause = GAME_SORT_CLAUSES.get(sort, GAME_DEFAULT_SORT)

    cursor = await db.execute(f"""
        SELECT g.* FROM games g
        ORDER BY {order_clause}
    """)

    columns = [desc[0] for desc in cursor.description]
    rows = await cursor.fetchall()
    return [dict(zip(columns, row)) for row in rows]


async def get_game(game_id: int) -> dict | None:
    """Get a single game by ID."""
    db = await get_db()
    cursor = await db.execute("SELECT * FROM games WHERE id = ?", (game_id,))
    columns = [desc[0] for desc in cursor.description]
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(zip(columns, row))


async def get_game_by_name(name: str) -> dict | None:
    """Get a game by display name (case-insensitive).

    Case-insensitive matching is what prevents the Special K importer from
    creating ``Cyberpunk 2077`` alongside an existing ``CYBERPUNK 2077``
    or ``cyberpunk 2077``. Manual create routes also use this for the
    duplicate-name guard, so two games with the same name (modulo case)
    never coexist.
    """
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM games WHERE LOWER(name) = LOWER(?) LIMIT 1",
        (name,),
    )
    columns = [desc[0] for desc in cursor.description]
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(zip(columns, row))


async def get_game_by_steam_app_id(app_id: int) -> dict | None:
    """Get a game by its Steam app ID."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM games WHERE steam_app_id = ?", (app_id,)
    )
    columns = [desc[0] for desc in cursor.description]
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(zip(columns, row))


async def create_game(
    name: str,
    steam_app_id: int | None = None,
    **extra_fields,
) -> dict:
    """Create a new game and its directory structure.

    Returns the created game dict.
    """
    db = await get_db()

    folder_name = sanitize_folder_name(name)

    # Ensure unique folder name
    existing_dir = get_game_dir(folder_name)
    counter = 1
    original_folder = folder_name
    while existing_dir.exists():
        folder_name = f"{original_folder} ({counter})"
        existing_dir = get_game_dir(folder_name)
        counter += 1

    # Create directories on disk
    ensure_game_directories(folder_name)

    # Insert into database
    fields = {
        "name": name,
        "folder_name": folder_name,
        "steam_app_id": steam_app_id,
        **{k: v for k, v in extra_fields.items() if v is not None},
    }

    columns = ", ".join(fields.keys())
    placeholders = ", ".join(["?"] * len(fields))
    values = list(fields.values())

    cursor = await db.execute(
        f"INSERT INTO games ({columns}) VALUES ({placeholders})",
        values,
    )
    await db.commit()

    return await get_game(cursor.lastrowid)


async def update_game(game_id: int, **fields) -> dict | None:
    """Update game fields. Only non-None values are updated."""
    db = await get_db()

    updates = {k: v for k, v in fields.items() if v is not None}
    if not updates:
        return await get_game(game_id)

    updates["updated_at"] = "datetime('now')"

    set_clauses = []
    values = []
    for key, value in updates.items():
        if value == "datetime('now')":
            set_clauses.append(f"{key} = datetime('now')")
        else:
            set_clauses.append(f"{key} = ?")
            values.append(value)

    values.append(game_id)
    await db.execute(
        f"UPDATE games SET {', '.join(set_clauses)} WHERE id = ?",
        values,
    )
    await db.commit()

    return await get_game(game_id)


async def delete_game(game_id: int) -> bool:
    """Delete a game and all its screenshots from DB.

    Note: Does NOT delete files from disk (that's a manual operation for safety).
    """
    db = await get_db()

    game = await get_game(game_id)
    if game is None:
        return False

    await db.execute("DELETE FROM games WHERE id = ?", (game_id,))
    await db.commit()
    return True


async def update_screenshot_stats(game_id: int, *, commit: bool = True) -> None:
    """Recalculate denormalized screenshot stats for a game."""
    db = await get_db()
    game_id = await resolve_game_id(game_id)

    cursor = await db.execute(
        """SELECT
            COUNT(DISTINCT COALESCE(capture_group_id, id)) as count,
            MIN(taken_at) as first_date,
            MAX(taken_at) as last_date
        FROM screenshots
        WHERE game_id = ?""",
        (game_id,),
    )
    row = await cursor.fetchone()
    count, first_date, last_date = row

    await db.execute(
        """UPDATE games SET
            screenshot_count = ?,
            first_screenshot_date = ?,
            last_screenshot_date = ?,
            updated_at = datetime('now')
        WHERE id = ?""",
        (count, first_date, last_date, game_id),
    )
    if commit:
        await db.commit()


async def get_or_create_game(
    name: str,
    steam_app_id: int | None = None,
    **extra_fields,
) -> dict:
    """Get an existing game or create a new one.

    Matches on steam_app_id first (if provided), then name.
    """
    if steam_app_id:
        game = await get_game_by_steam_app_id(steam_app_id)
        if game:
            return game

    game = await get_game_by_name(name)
    if game:
        return game

    return await create_game(name, steam_app_id, **extra_fields)


@measure_async("database", "game_list_public", threshold_ms=250)
async def list_public_games(sort: str = "name") -> list[dict]:
    """List all public games with sorting."""
    db = await get_db()

    order_clause = GAME_SORT_CLAUSES.get(sort, GAME_DEFAULT_SORT)

    cursor = await db.execute(f"""
        SELECT g.* FROM games g
        WHERE g.is_public = 1
        ORDER BY {order_clause}
    """)

    columns = [desc[0] for desc in cursor.description]
    rows = await cursor.fetchall()
    return [dict(zip(columns, row)) for row in rows]


async def resolve_game_id(game_id: int) -> int:
    db = await get_db()
    cursor = await db.execute("SELECT target_id FROM game_redirects WHERE source_id = ?", (game_id,))
    row = await cursor.fetchone()
    return row[0] if row else game_id


async def merge_games(source_id: int, target_id: int) -> dict:
    """Atomically combine records, leaving irreplaceable originals at their paths."""
    from backend.database import transaction
    from backend.services.screenshot_service import _sync_fts

    if source_id == target_id:
        raise ValueError("Cannot merge a game into itself")
    async with transaction() as db:
        source = await get_game(source_id)
        target = await get_game(target_id)
        if not source:
            raise ValueError(f"Source game {source_id} not found")
        if not target:
            raise ValueError(f"Target game {target_id} not found")
        cursor = await db.execute("SELECT id FROM screenshots WHERE game_id = ?", (source_id,))
        ids = [row[0] for row in await cursor.fetchall()]
        await db.execute(
            "UPDATE screenshots SET game_id = ?, updated_at = datetime('now') WHERE game_id = ?",
            (target_id, source_id),
        )
        # Running workers may still hold the old game ID and file paths.
        await db.execute("UPDATE upload_jobs SET game_id = ? WHERE game_id = ?", (target_id, source_id))
        await db.execute("UPDATE duplicate_candidates SET game_id = ? WHERE game_id = ?", (target_id, source_id))
        await db.execute("UPDATE game_redirects SET target_id = ? WHERE target_id = ?", (target_id, source_id))
        await db.execute("INSERT INTO game_redirects VALUES (?, ?)", (source_id, target_id))
        if not target.get("cover_image_path") and source.get("cover_image_path"):
            await db.execute("UPDATE games SET cover_image_path = ? WHERE id = ?",
                             (source["cover_image_path"], target_id))
        for screenshot_id in ids:
            await _sync_fts(screenshot_id, commit=False)
        await update_screenshot_stats(target_id, commit=False)
        await db.execute("DELETE FROM games WHERE id = ?", (source_id,))
    return {"moved": len(ids), "had_collisions": 0, "source_id": source_id,
            "target_id": target_id, "target_name": target["name"]}


async def save_cover_image(game_id: int, image_data: bytes, filename: str = "cover.jpg") -> str:
    """Save cover art for a game and update the database.

    Returns the relative path to the cover image.
    """
    game = await get_game(game_id)
    if not game:
        raise ValueError(f"Game {game_id} not found")

    cover_path = get_game_dir(game["folder_name"]) / filename
    cover_path.write_bytes(image_data)

    rel_path = f"{game['folder_name']}/{filename}"
    await update_game(game_id, cover_image_path=rel_path)
    return rel_path
