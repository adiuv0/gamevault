"""Durable large-preview generation for existing screenshot libraries."""

import asyncio
import logging
from pathlib import Path

from backend.database import get_db
from backend.services.hdr_processor import is_hdr_source
from backend.services.filesystem import safe_library_path, thumbnail_cache_path
from backend.services.image_processor import generate_thumbnails_with_hash

JOB_KIND = "preview_backfill"
BACKFILL_BATCH_SIZE = 10

_backfill_lock = asyncio.Lock()
logger = logging.getLogger(__name__)


async def _get_job(job_id: int) -> dict:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM maintenance_jobs WHERE id = ?", (job_id,))
    row = await cursor.fetchone()
    if row is None:
        raise ValueError(f"Maintenance job {job_id} not found")
    return dict(zip((column[0] for column in cursor.description), row))


async def get_latest_backfill_job() -> dict | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM maintenance_jobs WHERE kind = ? ORDER BY id DESC LIMIT 1",
        (JOB_KIND,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(zip((column[0] for column in cursor.description), row))


async def start_backfill_job() -> dict:
    """Create a full cache audit or resume an interrupted one."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT * FROM maintenance_jobs
           WHERE kind = ? AND status IN ('pending', 'running', 'failed')
           ORDER BY id DESC LIMIT 1""",
        (JOB_KIND,),
    )
    row = await cursor.fetchone()
    if row is not None:
        job = dict(zip((column[0] for column in cursor.description), row))
        await db.execute(
            """UPDATE maintenance_jobs SET status = 'pending', error_message = NULL,
                   updated_at = datetime('now') WHERE id = ?""",
            (job["id"],),
        )
        await db.commit()
        job["status"] = "pending"
        job["error_message"] = None
        return job

    cursor = await db.execute("SELECT COUNT(*) FROM screenshots")
    total = (await cursor.fetchone())[0]
    cursor = await db.execute(
        "INSERT INTO maintenance_jobs (kind, total) VALUES (?, ?)",
        (JOB_KIND, total),
    )
    await db.commit()
    return await _get_job(cursor.lastrowid)


async def run_backfill_job(job_id: int) -> None:
    """Audit preview cache entries and generate missing files in bounded batches."""
    if _backfill_lock.locked():
        return
    async with _backfill_lock:
        db = await get_db()
        await db.execute(
            """UPDATE maintenance_jobs SET status = 'running', updated_at = datetime('now')
               WHERE id = ?""",
            (job_id,),
        )
        await db.commit()

        try:
            while True:
                job = await _get_job(job_id)
                cursor = await db.execute(
                    """SELECT s.id, s.file_path, s.filename, s.preview_path,
                              s.thumbnail_path_sm, s.thumbnail_path_md,
                              g.folder_name
                       FROM screenshots s
                       JOIN games g ON g.id = s.game_id
                       WHERE s.id > ?
                       ORDER BY s.id LIMIT ?""",
                    (job["cursor_id"], BACKFILL_BATCH_SIZE),
                )
                columns = [column[0] for column in cursor.description]
                assets = [dict(zip(columns, row)) for row in await cursor.fetchall()]
                if not assets:
                    break

                errors = 0
                for asset in assets:
                    try:
                        await _ensure_preview(asset)
                        await db.commit()
                    except Exception as error:
                        await db.rollback()
                        errors += 1
                        logger.warning(
                            "Preview backfill failed for screenshot %s: %s",
                            asset["id"],
                            error,
                        )

                await db.execute(
                    """UPDATE maintenance_jobs SET cursor_id = ?, processed = processed + ?,
                           errors = errors + ?, updated_at = datetime('now') WHERE id = ?""",
                    (assets[-1]["id"], len(assets), errors, job_id),
                )
                await db.commit()

            await db.execute(
                """UPDATE maintenance_jobs SET status = 'completed',
                       completed_at = datetime('now'), updated_at = datetime('now')
                   WHERE id = ?""",
                (job_id,),
            )
            await db.commit()
        except Exception as error:
            await db.execute(
                """UPDATE maintenance_jobs SET status = 'failed', error_message = ?,
                       updated_at = datetime('now') WHERE id = ?""",
                (str(error), job_id),
            )
            await db.commit()


async def _ensure_preview(asset: dict) -> None:
    db = await get_db()
    existing_paths = [thumbnail_cache_path(asset.get(field)) for field in
                      ("preview_path", "thumbnail_path_sm", "thumbnail_path_md")]
    if all(path is not None and path.name.startswith("v2-") and path.exists()
           for path in existing_paths):
        return

    source_path = safe_library_path(asset.get("file_path"))
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    sm, md, preview_path, phash = await asyncio.to_thread(
        generate_thumbnails_with_hash,
        source_path,
        asset["folder_name"],
        Path(asset["filename"]).stem,
    )
    if not all((sm, md, preview_path)):
        raise ValueError(f"Could not generate preview for {source_path}")
    is_hdr = await asyncio.to_thread(is_hdr_source, source_path)
    await db.execute(
        """UPDATE screenshots SET thumbnail_path_sm = ?, thumbnail_path_md = ?,
           preview_path = ?, perceptual_hash = ?, is_hdr = ?, updated_at = datetime('now') WHERE id = ?""",
        (sm, md, preview_path, phash, is_hdr, asset["id"]),
    )
