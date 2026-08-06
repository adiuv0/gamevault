"""Screenshot management service: CRUD, file management, FTS sync."""

from pathlib import Path

from backend.config import settings
from backend.database import get_db
from backend.services.game_service import update_screenshot_stats


def _hash_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


async def _find_capture_group(fields: dict) -> int | None:
    """Find a high-confidence logical capture match for a new asset."""
    db = await get_db()
    game_id = fields["game_id"]
    source_key = fields.get("source_key")
    if source_key:
        cursor = await db.execute(
            """SELECT COALESCE(capture_group_id, id) FROM screenshots
               WHERE game_id = ? AND source_key = ? ORDER BY id LIMIT 1""",
            (game_id, source_key),
        )
        row = await cursor.fetchone()
        if row:
            return row[0]

    perceptual_hash = fields.get("perceptual_hash")
    width = fields.get("width")
    height = fields.get("height")
    taken_at = fields.get("taken_at")
    if not perceptual_hash or not width or not height or not taken_at:
        return None

    cursor = await db.execute(
        """SELECT COALESCE(capture_group_id, id), perceptual_hash
           FROM screenshots
           WHERE game_id = ? AND width = ? AND height = ?
             AND perceptual_hash IS NOT NULL
             AND ABS(strftime('%s', taken_at) - strftime('%s', ?)) <= 10
           ORDER BY id DESC LIMIT 50""",
        (game_id, width, height, taken_at),
    )
    for capture_group_id, candidate_hash in await cursor.fetchall():
        if _hash_distance(perceptual_hash, candidate_hash) <= 4:
            return capture_group_id
    return None


# GV-013: explicit allowlist for ORDER BY fragments — see game_service for
# the rationale.
SCREENSHOT_SORT_CLAUSES: dict[str, str] = {
    "date_asc": "MIN(taken_at) ASC, MIN(id) ASC",
    "date_desc": "MAX(taken_at) DESC, MAX(id) DESC",
    "name": "MIN(filename) ASC, MIN(id) ASC",
}
SCREENSHOT_DEFAULT_SORT = SCREENSHOT_SORT_CLAUSES["date_desc"]

_VARIANT_PREFERENCE = """CASE
    WHEN lower(COALESCE(s.format, '')) IN ('jxr', 'wdp') THEN 1
    ELSE 0
END, s.id"""


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

    order_clause = SCREENSHOT_SORT_CLAUSES.get(sort, SCREENSHOT_DEFAULT_SORT)

    # Paginate logical captures, not physical variants.
    cursor = await db.execute(
        """SELECT COUNT(DISTINCT COALESCE(capture_group_id, id))
           FROM screenshots WHERE game_id = ?""",
        (game_id,),
    )
    total = (await cursor.fetchone())[0]

    # Page compact group IDs first so only variants for the visible captures
    # are materialized below.
    offset = (page - 1) * limit
    cursor = await db.execute(
        f"""SELECT COALESCE(capture_group_id, id) AS group_id
            FROM screenshots
            WHERE game_id = ?
            GROUP BY COALESCE(capture_group_id, id)
            ORDER BY {order_clause}
            LIMIT ? OFFSET ?""",
        (game_id, limit, offset),
    )
    group_ids = [row[0] for row in await cursor.fetchall()]
    if not group_ids:
        return [], total

    placeholders = ", ".join("?" for _ in group_ids)
    cursor = await db.execute(
        f"""SELECT s.*,
                   EXISTS(
                       SELECT 1 FROM annotations a WHERE a.screenshot_id = s.id
                   ) AS has_annotation
            FROM screenshots s
            WHERE s.game_id = ?
              AND COALESCE(s.capture_group_id, s.id) IN ({placeholders})
            ORDER BY COALESCE(s.capture_group_id, s.id), {_VARIANT_PREFERENCE}""",
        (game_id, *group_ids),
    )
    variant_columns = [desc[0] for desc in cursor.description]
    variants_by_group: dict[int, list[dict]] = {group_id: [] for group_id in group_ids}
    for row in await cursor.fetchall():
        variant = dict(zip(variant_columns, row))
        group_id = variant.get("capture_group_id") or variant["id"]
        variants_by_group.setdefault(group_id, []).append(variant)

    screenshots = []
    for group_id in group_ids:
        variants = variants_by_group.get(group_id, [])
        if not variants:
            continue
        screenshot = dict(variants[0])
        screenshot["variants"] = variants
        screenshot["variant_count"] = len(variants)
        screenshot["is_favorite"] = any(item.get("is_favorite") for item in variants)
        screenshot["has_annotation"] = any(item.get("has_annotation") for item in variants)
        screenshots.append(screenshot)

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


async def get_screenshot_media(screenshot_id: int) -> dict | None:
    """Get only fields required to serve screenshot media."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, file_path, thumbnail_path_sm, thumbnail_path_md, format
           FROM screenshots WHERE id = ?""",
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
    recalculate_stats: bool = True,
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

    if "capture_group_id" not in fields:
        capture_group_id = await _find_capture_group(fields)
        if capture_group_id is not None:
            fields["capture_group_id"] = capture_group_id

    columns = ", ".join(fields.keys())
    placeholders = ", ".join(["?"] * len(fields))
    values = list(fields.values())

    cursor = await db.execute(
        f"INSERT INTO screenshots ({columns}) VALUES ({placeholders})",
        values,
    )
    screenshot_id = cursor.lastrowid
    if fields.get("capture_group_id") is None:
        await db.execute(
            "UPDATE screenshots SET capture_group_id = ? WHERE id = ?",
            (screenshot_id, screenshot_id),
        )
    # Sync FTS index
    await _sync_fts(screenshot_id, commit=False)

    # Update game stats
    if recalculate_stats:
        await update_screenshot_stats(game_id, commit=False)

    await db.commit()

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


async def _get_capture_identity(screenshot_id: int) -> tuple[int, int] | None:
    db = await get_db()
    cursor = await db.execute(
        """SELECT game_id, COALESCE(capture_group_id, id)
           FROM screenshots WHERE id = ?""",
        (screenshot_id,),
    )
    row = await cursor.fetchone()
    return (row[0], row[1]) if row else None


async def _get_capture_member_ids(game_id: int, capture_group_id: int) -> list[int]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT id FROM screenshots
           WHERE game_id = ? AND COALESCE(capture_group_id, id) = ?
           ORDER BY id""",
        (game_id, capture_group_id),
    )
    return [row[0] for row in await cursor.fetchall()]


async def toggle_favorite(screenshot_id: int) -> bool:
    """Toggle favorite status for the logical capture."""
    db = await get_db()
    identity = await _get_capture_identity(screenshot_id)
    if not identity:
        raise ValueError(f"Screenshot {screenshot_id} not found")

    game_id, capture_group_id = identity
    cursor = await db.execute(
        """SELECT MAX(is_favorite) FROM screenshots
           WHERE game_id = ? AND COALESCE(capture_group_id, id) = ?""",
        (game_id, capture_group_id),
    )
    new_value = 0 if (await cursor.fetchone())[0] else 1
    await db.execute(
        """UPDATE screenshots SET is_favorite = ?, updated_at = datetime('now')
           WHERE game_id = ? AND COALESCE(capture_group_id, id) = ?""",
        (new_value, game_id, capture_group_id),
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
    """Get the annotation attached to a logical capture."""
    db = await get_db()
    identity = await _get_capture_identity(screenshot_id)
    if not identity:
        return None
    game_id, capture_group_id = identity
    cursor = await db.execute(
        """SELECT a.* FROM annotations a
           JOIN screenshots s ON s.id = a.screenshot_id
           WHERE s.game_id = ? AND COALESCE(s.capture_group_id, s.id) = ?
           ORDER BY CASE WHEN s.id = ? THEN 0 ELSE 1 END,
                    a.updated_at DESC, a.id DESC
           LIMIT 1""",
        (game_id, capture_group_id, screenshot_id),
    )
    columns = [desc[0] for desc in cursor.description]
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(zip(columns, row))


async def save_annotation(screenshot_id: int, content: str, content_html: str | None = None) -> dict:
    """Create or update the annotation for a logical capture."""
    db = await get_db()

    identity = await _get_capture_identity(screenshot_id)
    if not identity:
        raise ValueError(f"Screenshot {screenshot_id} not found")
    member_ids = await _get_capture_member_ids(*identity)
    existing = await get_annotation(screenshot_id)
    if existing:
        await db.execute(
            """UPDATE annotations SET content = ?, content_html = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (content, content_html, existing["id"]),
        )
        await db.execute(
            f"""DELETE FROM annotations
                WHERE screenshot_id IN ({', '.join('?' for _ in member_ids)})
                  AND id != ?""",
            (*member_ids, existing["id"]),
        )
    else:
        await db.execute(
            "INSERT INTO annotations (screenshot_id, content, content_html) VALUES (?, ?, ?)",
            (screenshot_id, content, content_html),
        )
    # Re-sync every physical member so stale pre-group annotations disappear.
    for member_id in member_ids:
        await _sync_fts(member_id, commit=False)
    await db.commit()

    return await get_annotation(screenshot_id)


async def delete_annotation(screenshot_id: int) -> bool:
    """Delete every annotation attached to a logical capture."""
    db = await get_db()
    identity = await _get_capture_identity(screenshot_id)
    if not identity:
        return False
    game_id, capture_group_id = identity
    member_ids = await _get_capture_member_ids(game_id, capture_group_id)
    cursor = await db.execute(
        f"DELETE FROM annotations WHERE screenshot_id IN ({', '.join('?' for _ in member_ids)})",
        member_ids,
    )
    # Re-sync FTS without annotation
    for member_id in member_ids:
        await _sync_fts(member_id, commit=False)
    await db.commit()

    return cursor.rowcount > 0


# ── FTS sync ─────────────────────────────────────────────────────────────────

async def _sync_fts(screenshot_id: int, *, commit: bool = True) -> None:
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
    if commit:
        await db.commit()
