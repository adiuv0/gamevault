"""Screenshot management service: CRUD, file management, FTS sync."""

from pathlib import Path

from backend.config import settings
from backend.database import get_db
from backend.services.game_service import update_screenshot_stats


async def list_screenshots(
    game_id: int,
    page: int = 1,
    limit: int = 50,
    sort: str = "date_desc",
) -> tuple[list[dict], int]:
    """List screenshots for a game with pagination.

    Returns (screenshots_list, total_count).
    """
    db = await get_db()

    order_clause = {
        "date_asc": "s.taken_at ASC, s.id ASC",
        "date_desc": "s.taken_at DESC, s.id DESC",
        "name": "s.filename ASC",
    }.get(sort, "s.taken_at DESC, s.id DESC")

    # Get total count
    cursor = await db.execute(
        "SELECT COUNT(*) FROM screenshots WHERE game_id = ?",
        (game_id,),
    )
    total = (await cursor.fetchone())[0]

    # Get page of results
    offset = (page - 1) * limit
    cursor = await db.execute(
        f"""SELECT s.*,
            EXISTS(SELECT 1 FROM annotations a WHERE a.screenshot_id = s.id) as has_annotation
        FROM screenshots s
        WHERE s.game_id = ?
        ORDER BY {order_clause}
        LIMIT ? OFFSET ?""",
        (game_id, limit, offset),
    )
    columns = [desc[0] for desc in cursor.description]
    rows = await cursor.fetchall()
    screenshots = [dict(zip(columns, row)) for row in rows]

    return screenshots, total


async def get_screenshot(screenshot_id: int) -> dict | None:
    """Get a single screenshot with annotation flag."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT s.*,
            EXISTS(SELECT 1 FROM annotations a WHERE a.screenshot_id = s.id) as has_annotation
        FROM screenshots s
        WHERE s.id = ?""",
        (screenshot_id,),
    )
    columns = [desc[0] for desc in cursor.description]
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(zip(columns, row))


async def create_screenshot(
    game_id: int,
    filename: str,
    file_path: str,
    source: str = "upload",
    **extra_fields,
) -> dict:
    """Create a screenshot record in the database and sync FTS index."""
    db = await get_db()

    fields = {
        "game_id": game_id,
        "filename": filename,
        "file_path": file_path,
        "source": source,
        **{k: v for k, v in extra_fields.items() if v is not None},
    }

    columns = ", ".join(fields.keys())
    placeholders = ", ".join(["?"] * len(fields))
    values = list(fields.values())

    cursor = await db.execute(
        f"INSERT INTO screenshots ({columns}) VALUES ({placeholders})",
        values,
    )
    screenshot_id = cursor.lastrowid
    await db.commit()

    # Sync FTS index
    await _sync_fts(screenshot_id)

    # Update game stats
    await update_screenshot_stats(game_id)

    return await get_screenshot(screenshot_id)


async def update_screenshot(screenshot_id: int, **fields) -> dict | None:
    """Update screenshot fields."""
    db = await get_db()

    updates = {k: v for k, v in fields.items() if v is not None}
    if not updates:
        return await get_screenshot(screenshot_id)

    set_clauses = []
    values = []
    for key, value in updates.items():
        set_clauses.append(f"{key} = ?")
        values.append(value)

    set_clauses.append("updated_at = datetime('now')")
    values.append(screenshot_id)

    await db.execute(
        f"UPDATE screenshots SET {', '.join(set_clauses)} WHERE id = ?",
        values,
    )
    await db.commit()

    return await get_screenshot(screenshot_id)


async def delete_screenshot(screenshot_id: int) -> bool:
    """Delete a screenshot record (FTS cleanup handled by cascade)."""
    db = await get_db()

    screenshot = await get_screenshot(screenshot_id)
    if not screenshot:
        return False

    game_id = screenshot["game_id"]

    # Remove FTS entry
    await db.execute(
        "DELETE FROM screenshots_fts_content WHERE rowid = ?",
        (screenshot_id,),
    )

    # Remove screenshot
    await db.execute("DELETE FROM screenshots WHERE id = ?", (screenshot_id,))
    await db.commit()

    # Update game stats
    await update_screenshot_stats(game_id)

    return True


async def toggle_favorite(screenshot_id: int) -> bool:
    """Toggle favorite status. Returns new is_favorite value."""
    db = await get_db()
    screenshot = await get_screenshot(screenshot_id)
    if not screenshot:
        raise ValueError(f"Screenshot {screenshot_id} not found")

    new_value = 0 if screenshot["is_favorite"] else 1
    await db.execute(
        "UPDATE screenshots SET is_favorite = ?, updated_at = datetime('now') WHERE id = ?",
        (new_value, screenshot_id),
    )
    await db.commit()
    return bool(new_value)


async def check_duplicate_hash(sha256_hash: str) -> dict | None:
    """Check if a screenshot with this hash already exists."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, game_id, filename FROM screenshots WHERE sha256_hash = ?",
        (sha256_hash,),
    )
    row = await cursor.fetchone()
    if row:
        return {"id": row[0], "game_id": row[1], "filename": row[2]}
    return None


async def check_duplicate_steam_id(steam_id: str) -> bool:
    """Check if a screenshot with this Steam ID already exists."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT 1 FROM screenshots WHERE steam_screenshot_id = ?",
        (steam_id,),
    )
    return await cursor.fetchone() is not None


async def check_hashes_batch(hashes: list[str]) -> set[str]:
    """Return the subset of hashes that already exist in the database."""
    if not hashes:
        return set()
    db = await get_db()
    existing: set[str] = set()
    # SQLite has a variable limit (~999), so batch in chunks
    chunk_size = 500
    for i in range(0, len(hashes), chunk_size):
        chunk = hashes[i : i + chunk_size]
        placeholders = ", ".join(["?"] * len(chunk))
        cursor = await db.execute(
            f"SELECT sha256_hash FROM screenshots WHERE sha256_hash IN ({placeholders})",
            chunk,
        )
        rows = await cursor.fetchall()
        existing.update(row[0] for row in rows)
    return existing


# ── Annotation helpers ───────────────────────────────────────────────────────

async def get_annotation(screenshot_id: int) -> dict | None:
    """Get annotation for a screenshot."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM annotations WHERE screenshot_id = ?",
        (screenshot_id,),
    )
    columns = [desc[0] for desc in cursor.description]
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(zip(columns, row))


async def save_annotation(screenshot_id: int, content: str, content_html: str | None = None) -> dict:
    """Create or update an annotation for a screenshot."""
    db = await get_db()

    existing = await get_annotation(screenshot_id)
    if existing:
        await db.execute(
            """UPDATE annotations SET content = ?, content_html = ?, updated_at = datetime('now')
               WHERE screenshot_id = ?""",
            (content, content_html, screenshot_id),
        )
    else:
        await db.execute(
            "INSERT INTO annotations (screenshot_id, content, content_html) VALUES (?, ?, ?)",
            (screenshot_id, content, content_html),
        )
    await db.commit()

    # Re-sync FTS with updated annotation
    await _sync_fts(screenshot_id)

    return await get_annotation(screenshot_id)


async def delete_annotation(screenshot_id: int) -> bool:
    """Delete annotation for a screenshot."""
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM annotations WHERE screenshot_id = ?",
        (screenshot_id,),
    )
    await db.commit()

    # Re-sync FTS without annotation
    await _sync_fts(screenshot_id)

    return cursor.rowcount > 0


# ── FTS sync ─────────────────────────────────────────────────────────────────

async def _sync_fts(screenshot_id: int) -> None:
    """Sync the FTS index for a screenshot."""
    db = await get_db()

    # Get screenshot + game name + annotation
    cursor = await db.execute(
        """SELECT s.filename, s.steam_description, g.name as game_name,
                  a.content as annotation_content
           FROM screenshots s
           JOIN games g ON g.id = s.game_id
           LEFT JOIN annotations a ON a.screenshot_id = s.id
           WHERE s.id = ?""",
        (screenshot_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return

    filename, steam_desc, game_name, annotation = row

    # Upsert into FTS content table (triggers handle the virtual table)
    await db.execute(
        """INSERT INTO screenshots_fts_content
           (rowid, game_name, filename, steam_description, annotation_content)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(rowid) DO UPDATE SET
               game_name = excluded.game_name,
               filename = excluded.filename,
               steam_description = excluded.steam_description,
               annotation_content = excluded.annotation_content""",
        (screenshot_id, game_name, filename, steam_desc or "", annotation or ""),
    )
    await db.commit()
