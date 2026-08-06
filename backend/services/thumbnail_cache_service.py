"""Migration helpers for moving legacy thumbnails onto fast cache storage."""

import asyncio
import shutil

from fastapi import HTTPException

from backend.database import get_db
from backend.services.filesystem import safe_library_path, thumbnail_cache_path


def _move_paths(paths: list[str]) -> dict[str, int]:
    result = {"moved": 0, "already_cached": 0, "missing": 0, "invalid": 0}
    for rel_path in paths:
        try:
            source = safe_library_path(rel_path)
            target = thumbnail_cache_path(rel_path)
        except HTTPException:
            result["invalid"] += 1
            continue

        if target is None:
            result["invalid"] += 1
        elif target.exists():
            result["already_cached"] += 1
        elif not source.exists():
            result["missing"] += 1
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            result["moved"] += 1
    return result


async def migrate_legacy_thumbnails() -> dict[str, int]:
    """Move all DB-referenced legacy thumbnails into cache storage."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT thumbnail_path_sm FROM screenshots WHERE thumbnail_path_sm IS NOT NULL
           UNION
           SELECT thumbnail_path_md FROM screenshots WHERE thumbnail_path_md IS NOT NULL"""
    )
    paths = [row[0] for row in await cursor.fetchall()]
    return await asyncio.to_thread(_move_paths, paths)
