"""Game management service: CRUD operations and folder management."""

import json
import logging
import os
import shutil
from pathlib import Path

from backend.config import settings
from backend.database import get_db
from backend.services.filesystem import (
    ensure_game_directories,
    sanitize_folder_name,
    get_game_dir,
)

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


async def update_screenshot_stats(game_id: int) -> None:
    """Recalculate denormalized screenshot stats for a game."""
    db = await get_db()

    cursor = await db.execute(
        """SELECT
            COUNT(*) as count,
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


async def merge_games(source_id: int, target_id: int) -> dict:
    """Merge ``source_id`` into ``target_id``: move every screenshot (DB
    rows + files on disk) into the target game, transfer the cover if the
    target lacks one, re-sync the FTS index, then delete the source game.

    Use case: the Special K importer's name-only matching can produce a
    duplicate game when its cleaned folder name disagrees with the Steam
    Store API's canonical name (e.g. ``Cyberpunk2077`` vs
    ``Cyberpunk 2077: Phantom Liberty``). Merging consolidates them after
    the fact.

    Filename collisions in the target are resolved by appending
    ``" (N)"`` before the extension. Annotations and share-links FK to
    screenshot IDs (not game IDs), so they ride along automatically.

    Returns a dict with ``moved`` (count), ``had_collisions`` (count),
    plus the source/target IDs and the resolved target name.
    """
    if source_id == target_id:
        raise ValueError("Cannot merge a game into itself")

    source = await get_game(source_id)
    target = await get_game(target_id)
    if not source:
        raise ValueError(f"Source game {source_id} not found")
    if not target:
        raise ValueError(f"Target game {target_id} not found")

    db = await get_db()

    # Pull every screenshot row attached to the source
    cursor = await db.execute(
        """SELECT id, filename, file_path, thumbnail_path_sm, thumbnail_path_md
           FROM screenshots WHERE game_id = ?""",
        (source_id,),
    )
    cols = [d[0] for d in cursor.description]
    rows = [dict(zip(cols, row)) for row in await cursor.fetchall()]

    target_folder = target["folder_name"]
    target_screenshots_dir = settings.library_dir / target_folder / "screenshots"
    target_thumb_sm_dir = settings.library_dir / target_folder / "thumbnails" / "300"
    target_thumb_md_dir = settings.library_dir / target_folder / "thumbnails" / "800"
    target_screenshots_dir.mkdir(parents=True, exist_ok=True)
    target_thumb_sm_dir.mkdir(parents=True, exist_ok=True)
    target_thumb_md_dir.mkdir(parents=True, exist_ok=True)

    moved = 0
    collisions = 0

    for ss in rows:
        # Resolve unique filename in the target folder (handle collision
        # by appending " (N)" before the extension).
        new_name = ss["filename"]
        if (target_screenshots_dir / new_name).exists():
            stem, ext = os.path.splitext(ss["filename"])
            counter = 1
            while True:
                candidate = f"{stem} ({counter}){ext}"
                if not (target_screenshots_dir / candidate).exists():
                    new_name = candidate
                    break
                counter += 1
            collisions += 1

        new_stem = Path(new_name).stem
        new_thumb_filename = f"{new_stem}.jpg"

        # Physical moves — best-effort. If a source file is missing we
        # still update the DB row so it points at the (intended) target
        # location; serving will 404 the same way it would now.
        old_main = (
            settings.library_dir / ss["file_path"] if ss["file_path"] else None
        )
        old_sm = (
            settings.library_dir / ss["thumbnail_path_sm"]
            if ss["thumbnail_path_sm"]
            else None
        )
        old_md = (
            settings.library_dir / ss["thumbnail_path_md"]
            if ss["thumbnail_path_md"]
            else None
        )

        new_main = target_screenshots_dir / new_name
        new_sm = target_thumb_sm_dir / new_thumb_filename
        new_md = target_thumb_md_dir / new_thumb_filename

        if old_main and old_main.exists():
            shutil.move(str(old_main), str(new_main))
        if old_sm and old_sm.exists():
            shutil.move(str(old_sm), str(new_sm))
        if old_md and old_md.exists():
            shutil.move(str(old_md), str(new_md))

        # Update DB row to point at the new game + new paths
        new_file_path = f"{target_folder}/screenshots/{new_name}"
        new_sm_rel = (
            f"{target_folder}/thumbnails/300/{new_thumb_filename}"
            if ss["thumbnail_path_sm"]
            else None
        )
        new_md_rel = (
            f"{target_folder}/thumbnails/800/{new_thumb_filename}"
            if ss["thumbnail_path_md"]
            else None
        )

        await db.execute(
            """UPDATE screenshots
               SET game_id = ?, filename = ?, file_path = ?,
                   thumbnail_path_sm = ?, thumbnail_path_md = ?,
                   updated_at = datetime('now')
               WHERE id = ?""",
            (target_id, new_name, new_file_path, new_sm_rel, new_md_rel, ss["id"]),
        )
        moved += 1

    await db.commit()

    # Transfer the cover image if the target doesn't have one. We move
    # rather than copy so the source folder ends up cleanly empty.
    if not target.get("cover_image_path") and source.get("cover_image_path"):
        old_cover = settings.library_dir / source["cover_image_path"]
        if old_cover.exists():
            new_cover = settings.library_dir / target_folder / old_cover.name
            try:
                shutil.move(str(old_cover), str(new_cover))
                await db.execute(
                    "UPDATE games SET cover_image_path = ? WHERE id = ?",
                    (f"{target_folder}/{old_cover.name}", target_id),
                )
                await db.commit()
            except OSError as e:
                logger.warning("merge_games: cover transfer failed: %s", e)

    # Re-sync the FTS index for every moved screenshot — game_name
    # appears in the searchable content table.
    from backend.services.screenshot_service import _sync_fts
    for ss in rows:
        await _sync_fts(ss["id"])

    # Refresh stats on the target now that screenshots have moved
    await update_screenshot_stats(target_id)

    # Drop the source DB row. Annotations + share_links FK to screenshot
    # IDs (not game IDs), so they stayed attached to the moved rows.
    await db.execute("DELETE FROM games WHERE id = ?", (source_id,))
    await db.commit()

    # Best-effort cleanup of the now-empty source folder on disk
    source_dir = settings.library_dir / source["folder_name"]
    if source_dir.exists():
        try:
            shutil.rmtree(source_dir)
        except OSError as e:
            logger.warning("merge_games: source folder cleanup failed: %s", e)

    target_fresh = await get_game(target_id)
    return {
        "moved": moved,
        "had_collisions": collisions,
        "source_id": source_id,
        "target_id": target_id,
        "target_name": (target_fresh or target).get("name", ""),
    }


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
