"""Date captures that were stored without a capture time.

Files uploaded before GameVault read XMP dates, and every Steam JPG copied out of
Steam's screenshot folder through the browser, were stored with ``taken_at``
NULL and sorted by their upload day. Special K and the Windows capture path
record the moment in an XMP packet (or, in older Special K builds, a PNG text
chunk) inside the file, so the time is still on disk. Migration m022 queues this
repair; it runs in the background after startup, then lets the duplicate scan
regroup the library, since captures that now have times pair by time.

A JPG with no date inside picks up the time of the copy it is grouped with
(``capture_matching.share_capture_time``), or the time GameVault Sync reports
for its hash.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from backend.database import get_db
from backend.services.capture_matching import share_capture_time
from backend.services.filesystem import safe_library_path
from backend.services.game_service import update_screenshot_stats
from backend.services.image_processor import embedded_capture_time
from backend.services.steam_capture_times import format_capture_time

logger = logging.getLogger(__name__)

JOB_KIND = "embedded_capture_times"
_BATCH_SIZE = 50
# A run that keeps failing is retried on the next few starts, then left alone.
MAX_ATTEMPTS = 3

_startup_job: asyncio.Task | None = None


def _read_time(rel_path: str | None) -> str | None:
    try:
        path = safe_library_path(rel_path)
    except Exception:  # noqa: BLE001 - an unsafe or empty path is just skipped
        return None
    captured = embedded_capture_time(path)
    return format_capture_time(captured) if captured else None


async def _pending_job_id() -> int | None:
    db = await get_db()
    cursor = await db.execute(
        """SELECT id FROM maintenance_jobs
           WHERE kind = ? AND status IN ('pending', 'running')
           ORDER BY id DESC LIMIT 1""",
        (JOB_KIND,),
    )
    row = await cursor.fetchone()
    return row[0] if row else None


async def resume_repair_job(then: Callable[[], Awaitable[None]] | None = None) -> None:
    """Start or continue a queued repair, then run ``then`` (the duplicate scan).

    The scan waits because captures dated by this repair pair by time; scanning
    first would leave them for the slower picture-only match, or apart.
    """
    global _startup_job
    job_id = await _pending_job_id()
    if job_id is None:
        if then is not None:
            await then()
        return

    async def run() -> None:
        await run_repair_job(job_id)
        if then is not None:
            await then()

    _startup_job = asyncio.create_task(run(), name="gamevault-embedded-capture-times")


async def stop_repair_job() -> None:
    """Cancel a running repair; it stays queued and resumes on the next start."""
    global _startup_job
    task, _startup_job = _startup_job, None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def run_repair_job(job_id: int) -> None:
    """Read the capture time out of every stored file that has none."""
    db = await get_db()
    await db.execute(
        "UPDATE maintenance_jobs SET status = 'running', updated_at = datetime('now') WHERE id = ?",
        (job_id,),
    )
    await db.commit()
    games: set[int] = set()
    try:
        while True:
            cursor = await db.execute("SELECT cursor_id FROM maintenance_jobs WHERE id = ?", (job_id,))
            (after,) = await cursor.fetchone()
            cursor = await db.execute(
                """SELECT id, game_id, file_path FROM screenshots
                   WHERE taken_at IS NULL AND id > ? ORDER BY id LIMIT ?""",
                (after, _BATCH_SIZE),
            )
            rows = await cursor.fetchall()
            if not rows:
                break
            times = await asyncio.to_thread(lambda: [_read_time(path) for _, _, path in rows])
            dated = 0
            for (screenshot_id, game_id, _), taken_at in zip(rows, times):
                if taken_at is None:
                    continue
                cursor = await db.execute(
                    "UPDATE screenshots SET taken_at = ? WHERE id = ? AND taken_at IS NULL",
                    (taken_at, screenshot_id),
                )
                if cursor.rowcount:
                    dated += 1
                    games.add(game_id)
            await db.execute(
                """UPDATE maintenance_jobs SET cursor_id = ?, processed = processed + ?,
                       updated_at = datetime('now') WHERE id = ?""",
                (rows[-1][0], dated, job_id),
            )
            await db.commit()

        # Copies already grouped with a copy that now has a time share it.
        cursor = await db.execute(
            """SELECT DISTINCT game_id, COALESCE(capture_group_id, id) FROM screenshots s
               WHERE taken_at IS NULL AND EXISTS (
                   SELECT 1 FROM screenshots t
                   WHERE t.game_id = s.game_id
                     AND COALESCE(t.capture_group_id, t.id) = COALESCE(s.capture_group_id, s.id)
                     AND t.taken_at IS NOT NULL)"""
        )
        for game_id, group_id in await cursor.fetchall():
            if await share_capture_time(db, game_id, group_id):
                games.add(game_id)
        await db.commit()
        for game_id in games:
            await update_screenshot_stats(game_id)
        await db.execute(
            """UPDATE maintenance_jobs SET status = 'completed', completed_at = datetime('now'),
                   updated_at = datetime('now') WHERE id = ?""",
            (job_id,),
        )
        await db.commit()
        logger.info("Capture time repair dated screenshots in %d game(s)", len(games))
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 - the job is retried on the next start
        logger.warning("Capture time repair did not finish: %s", error)
        await db.rollback()
        # For this job, ``errors`` counts the runs that could not finish.
        await db.execute(
            """UPDATE maintenance_jobs SET errors = errors + 1, error_message = ?,
                   status = CASE WHEN errors + 1 >= ? THEN 'failed' ELSE 'pending' END,
                   updated_at = datetime('now') WHERE id = ?""",
            (str(error)[:500], MAX_ATTEMPTS, job_id),
        )
        await db.commit()
