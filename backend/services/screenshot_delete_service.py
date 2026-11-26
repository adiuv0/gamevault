"""Delete captures, or one file of a capture, with their files and caches.

A capture is a logical screenshot: Steam's JPG and Special K's JXR/PNG of one
moment are grouped into one. Deleting a capture removes every file in it;
deleting a variant removes one file and keeps the rest, moving the note, share
link and favourite to a file that stays.

Rows go first, in one transaction, and the files after it commits: if the delete
fails, nothing points at missing originals. Each removed file's hash is kept in
``deleted_screenshots`` so GameVault Sync and the importers don't bring it back.
"""

import asyncio
from pathlib import Path

from fastapi import HTTPException

from backend.database import get_db, transaction
from backend.services import full_render_service
from backend.services.filesystem import (
    safe_library_path,
    safe_media_path,
    thumbnail_cache_path,
)
from backend.services.game_service import update_screenshot_stats

# Enough for a big selection in the grid, small enough for one transaction.
MAX_BULK_DELETE = 2000

_PATH_FIELDS = ("file_path", "thumbnail_path_sm", "thumbnail_path_md", "preview_path")


async def _rows(where: str, params: tuple) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(f"SELECT * FROM screenshots WHERE {where}", params)
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in await cursor.fetchall()]


async def _capture_rows(screenshot_id: int) -> list[dict]:
    """Every file of the capture ``screenshot_id`` belongs to."""
    return await _rows(
        """(game_id, COALESCE(capture_group_id, id)) IN (
               SELECT game_id, COALESCE(capture_group_id, id)
               FROM screenshots WHERE id = ?
           ) ORDER BY id""",
        (screenshot_id,),
    )


async def _in_use_elsewhere(doomed_ids: set[int]) -> tuple[set[str], set[str]]:
    """Paths and hashes still used by rows that aren't being deleted.

    One pass over the table: per-path lookups have no index to use and cost a
    scan each, which a multi-select delete would repeat thousands of times.
    """
    db = await get_db()
    paths: set[str] = set()
    hashes: set[str] = set()
    cursor = await db.execute(
        "SELECT id, sha256_hash, " + ", ".join(_PATH_FIELDS) + " FROM screenshots"
    )
    for screenshot_id, sha, *row_paths in await cursor.fetchall():
        if screenshot_id in doomed_ids:
            continue
        if sha:
            hashes.add(sha)
        paths.update(path for path in row_paths if path)
    return paths, hashes


async def _files_to_remove(rows: list[dict]) -> list[Path]:
    """Originals, thumbnails, previews and full renders no remaining row uses."""
    used_paths, used_hashes = await _in_use_elsewhere({row["id"] for row in rows})
    files: list[Path] = []
    seen: set[str] = set()
    for row in rows:
        for field in _PATH_FIELDS:
            rel_path = row.get(field)
            if not rel_path or rel_path in seen or rel_path in used_paths:
                continue
            seen.add(rel_path)
            try:
                resolver = safe_library_path if field == "file_path" else safe_media_path
                files.append(resolver(rel_path))
            except HTTPException:
                # An unsafe stored path: skip the unlink, still remove the row.
                continue

        # The full-size render is content-addressed like the preview, so a
        # duplicate of the same original still needs it.
        if not full_render_service.needs_full_render(row):
            continue
        if row.get("sha256_hash") in used_hashes or row.get("preview_path") in used_paths:
            continue
        try:
            rel = await asyncio.to_thread(full_render_service.full_render_rel_path, row)
            render = thumbnail_cache_path(rel) if rel else None
        except HTTPException:
            render = None
        if render is not None and str(render) not in seen:
            seen.add(str(render))
            files.append(render)
    return files


async def _hand_over_to(db, removed: dict, survivor_id: int) -> None:
    """Keep what belonged to the whole capture when one of its files goes."""
    # The capture's note reads from any of its files: move this file's note only
    # when the file that stays has none of its own.
    await db.execute(
        """UPDATE annotations SET screenshot_id = ?
           WHERE screenshot_id = ?
             AND NOT EXISTS (SELECT 1 FROM annotations WHERE screenshot_id = ?)""",
        (survivor_id, removed["id"], survivor_id),
    )
    await db.execute(
        "UPDATE share_links SET screenshot_id = ? WHERE screenshot_id = ?",
        (survivor_id, removed["id"]),
    )
    if removed.get("is_favorite"):
        await db.execute(
            "UPDATE screenshots SET is_favorite = 1 WHERE id = ?", (survivor_id,)
        )


async def _remove_rows(db, rows: list[dict]) -> None:
    for row in rows:
        sha = row.get("sha256_hash")
        if sha:
            await db.execute(
                """INSERT INTO deleted_screenshots (sha256_hash, filename, game_id)
                   VALUES (?, ?, ?)
                   ON CONFLICT(sha256_hash) DO UPDATE SET
                       filename = excluded.filename,
                       game_id = excluded.game_id,
                       deleted_at = datetime('now')""",
                (sha, row.get("filename"), row.get("game_id")),
            )
        await db.execute("DELETE FROM screenshots_fts_content WHERE rowid = ?", (row["id"],))
        await db.execute("DELETE FROM screenshots WHERE id = ?", (row["id"],))


def _unlink_all(files: list[Path]) -> int:
    removed = 0
    for path in files:
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError:
            # A file the container can't remove (permissions, a read-only share)
            # stays on disk; the screenshot is already gone from GameVault.
            continue
    return removed


async def delete_variant(screenshot_id: int) -> dict | None:
    """Delete one file of a capture. Returns None when it doesn't exist.

    When it was the capture's only file, the whole capture goes.
    """
    async with transaction():
        db = await get_db()
        members = await _capture_rows(screenshot_id)
        removed = next((row for row in members if row["id"] == screenshot_id), None)
        if removed is None:
            return None
        others = [row for row in members if row["id"] != screenshot_id]
        files = await _files_to_remove([removed])
        survivor_id = None
        if others:
            survivor_id = others[0]["id"]
            await _hand_over_to(db, removed, survivor_id)
            group_id = removed.get("capture_group_id") or removed["id"]
            if group_id == removed["id"]:
                # The group was named after this row. SQLite can hand a deleted id
                # to the next new row, which would then join this capture, so
                # name the group after a file that stays.
                await db.execute(
                    """UPDATE screenshots SET capture_group_id = ?
                       WHERE game_id = ? AND COALESCE(capture_group_id, id) = ?""",
                    (survivor_id, removed["game_id"], group_id),
                )
        await _remove_rows(db, [removed])
        await update_screenshot_stats(removed["game_id"], commit=False)

    unlinked = await asyncio.to_thread(_unlink_all, files)
    return {
        "deleted_files": 1,
        "deleted_captures": 0 if others else 1,
        "removed_from_disk": unlinked,
        "remaining_id": survivor_id,
    }


async def delete_captures(screenshot_ids: list[int]) -> dict:
    """Delete whole captures (every file in each). Unknown ids are skipped."""
    async with transaction():
        db = await get_db()
        rows: dict[int, dict] = {}
        captures: set[tuple[int, int]] = set()
        for screenshot_id in dict.fromkeys(screenshot_ids):
            if screenshot_id in rows:
                continue
            members = await _capture_rows(screenshot_id)
            if not members:
                continue
            first = members[0]
            captures.add((first["game_id"], first.get("capture_group_id") or first["id"]))
            for row in members:
                rows[row["id"]] = row
        doomed = list(rows.values())
        if not doomed:
            return {"deleted_files": 0, "deleted_captures": 0, "removed_from_disk": 0}
        files = await _files_to_remove(doomed)
        await _remove_rows(db, doomed)
        for game_id in {row["game_id"] for row in doomed}:
            await update_screenshot_stats(game_id, commit=False)

    unlinked = await asyncio.to_thread(_unlink_all, files)
    return {
        "deleted_files": len(doomed),
        "deleted_captures": len(captures),
        "removed_from_disk": unlinked,
    }
