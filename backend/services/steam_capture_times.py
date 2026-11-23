"""Give Steam imports the time they were taken instead of the time Steam posted them.

The Steam API only reports when a screenshot was posted. The time it was taken is
in the file name Steam keeps for the upload, which the image CDN hands out with
the file. New imports read it while downloading; this module looks it up for
screenshots already in the library, which arrived with the posted time.
"""

import asyncio
import logging
from datetime import datetime
from urllib.parse import urlparse

import httpx

from backend.config import settings
from backend.database import get_db
from backend.routers.settings import get_effective_key
from backend.services.game_service import update_screenshot_stats
from backend.services.steam_scraper import (
    SteamFetchError,
    SteamScraper,
    SteamScreenshot,
    capture_time_from_name,
)

logger = logging.getLogger(__name__)

JOB_KIND = "steam_capture_times"
# A run that Steam cut short is tried again on the next few starts, then left
# for the Steam import page, which repairs the same screenshots.
MAX_ATTEMPTS = 3
_BATCH_SIZE = 40
# Name lookups carry no image data, so more of them can be in flight than downloads.
_MIN_LOOKUP_CONCURRENCY = 4

# What a lookup can fail with when Steam is unreachable or answers badly.
_LOOKUP_ERRORS = (SteamFetchError, httpx.HTTPError, OSError)

_startup_job: asyncio.Task | None = None


def format_capture_time(captured: datetime) -> str:
    return captured.isoformat(timespec="seconds")


def needs_capture_time(row: dict) -> bool:
    """A Steam import that still carries Steam's posted time as its capture time."""
    return row.get("source") == "steam_import" and row.get("steam_posted_at") is None


async def get_steam_screenshot(steam_id: str) -> dict | None:
    """The library's row for a Steam screenshot, if it was imported before."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, game_id, source, taken_at, steam_posted_at
           FROM screenshots WHERE steam_screenshot_id = ? ORDER BY id LIMIT 1""",
        (steam_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(zip((d[0] for d in cursor.description), row))


async def lookup_capture_time(
    scraper: SteamScraper, screenshot: SteamScreenshot, posted_at: str | None
) -> datetime | None:
    """When a listed Steam screenshot was taken, or None if Steam cannot say.

    Raises SteamFetchError when Steam could not be asked at all.
    """
    captured = capture_time_from_name(screenshot.original_name, posted_at)
    if captured:
        return captured
    # The thumbnail is a separate upload named after the same moment, so it
    # serves when a listing did not come with the full image's address.
    url = screenshot.full_image_url
    if not url and screenshot.thumbnail_url:
        parsed = urlparse(screenshot.thumbnail_url)
        url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if not url:
        return None
    return capture_time_from_name(await scraper.fetch_original_name(url), posted_at)


async def apply_capture_time(row: dict, captured: datetime | None, posted_at: str | None) -> bool:
    """Record a lookup's result on a row. Returns whether its capture time changed.

    The caller commits. A lookup that found nothing still marks the row so it
    is not asked about again.
    """
    db = await get_db()
    previous = row["taken_at"]
    taken_at = format_capture_time(captured) if captured else previous
    cursor = await db.execute(
        """UPDATE screenshots SET taken_at = ?, steam_posted_at = ?
           WHERE id = ? AND steam_posted_at IS NULL""",
        (taken_at, posted_at or previous or "", row["id"]),
    )
    return cursor.rowcount == 1 and taken_at != previous


async def repair_existing(
    scraper: SteamScraper, screenshot: SteamScreenshot, row: dict
) -> bool:
    """Fix the capture time of an already imported screenshot met during an import."""
    if not needs_capture_time(row):
        return False
    posted_at = screenshot.date_taken or row["taken_at"]
    try:
        captured = await lookup_capture_time(scraper, screenshot, posted_at)
    except _LOOKUP_ERRORS as error:
        logger.warning(
            "Could not look up the capture time of Steam screenshot %s: %s",
            screenshot.screenshot_id, error,
        )
        return False
    db = await get_db()
    changed = await apply_capture_time(row, captured, posted_at)
    await db.commit()
    return changed


# ── Background repair queued by migration m018 ───────────────────────────────


async def resume_repair_job() -> None:
    """Start the repair a migration queued, or continue one a restart interrupted."""
    global _startup_job
    db = await get_db()
    cursor = await db.execute(
        """SELECT id FROM maintenance_jobs
           WHERE kind = ? AND status IN ('pending', 'running')
           ORDER BY id DESC LIMIT 1""",
        (JOB_KIND,),
    )
    row = await cursor.fetchone()
    if row is not None:
        _startup_job = asyncio.create_task(
            run_repair_job(row[0]), name="gamevault-steam-capture-times"
        )


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


async def _pending_rows() -> dict[str, dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, game_id, source, taken_at, steam_posted_at, steam_screenshot_id
           FROM screenshots
           WHERE source = 'steam_import' AND steam_screenshot_id IS NOT NULL
             AND steam_posted_at IS NULL"""
    )
    columns = [d[0] for d in cursor.description]
    return {row[5]: dict(zip(columns, row)) for row in await cursor.fetchall()}


async def _apps_of(rows: dict[str, dict]) -> list[int]:
    game_ids = sorted({row["game_id"] for row in rows.values()})
    if not game_ids:
        return []
    db = await get_db()
    cursor = await db.execute(
        f"""SELECT DISTINCT steam_app_id FROM games
            WHERE steam_app_id IS NOT NULL
              AND id IN ({", ".join("?" for _ in game_ids)})""",
        game_ids,
    )
    return [row[0] for row in await cursor.fetchall()]


async def _steam_users() -> list[str]:
    """Profiles screenshots were imported from, most recent first."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT steam_user_id FROM steam_import_sessions
           WHERE steam_user_id IS NOT NULL AND steam_user_id != ''
           GROUP BY steam_user_id ORDER BY MAX(id) DESC"""
    )
    return [row[0] for row in await cursor.fetchall()]


async def _set_job(job_id: int, status: str, error_message: str | None = None) -> None:
    db = await get_db()
    await db.execute(
        """UPDATE maintenance_jobs SET status = ?, error_message = ?,
               completed_at = CASE WHEN ? = 'completed' THEN datetime('now') END,
               updated_at = datetime('now')
           WHERE id = ?""",
        (status, error_message, status, job_id),
    )
    await db.commit()


async def _repair_app(
    scraper: SteamScraper, app_id: int, pending: dict[str, dict], job_id: int
) -> int:
    """Look up every pending screenshot Steam lists for one game. Returns lookups that failed."""
    listed = await scraper.get_game_screenshots(app_id)
    todo = [shot for shot in listed if shot.screenshot_id in pending]
    if not todo:
        return 0

    semaphore = asyncio.Semaphore(max(settings.import_concurrency, _MIN_LOOKUP_CONCURRENCY))

    async def lookup(shot: SteamScreenshot):
        posted_at = shot.date_taken or pending[shot.screenshot_id]["taken_at"]
        async with semaphore:
            try:
                return shot, posted_at, await lookup_capture_time(scraper, shot, posted_at)
            except _LOOKUP_ERRORS as error:
                return shot, posted_at, error

    db = await get_db()
    failed = 0
    games: set[int] = set()
    for start in range(0, len(todo), _BATCH_SIZE):
        results = await asyncio.gather(*(lookup(shot) for shot in todo[start:start + _BATCH_SIZE]))
        fixed = 0
        for shot, posted_at, outcome in results:
            if isinstance(outcome, Exception):
                failed += 1
                continue
            row = pending.pop(shot.screenshot_id)
            if await apply_capture_time(row, outcome, posted_at):
                fixed += 1
                games.add(row["game_id"])
        await db.execute(
            """UPDATE maintenance_jobs SET processed = processed + ?,
                   updated_at = datetime('now') WHERE id = ?""",
            (fixed, job_id),
        )
        await db.commit()
    for game_id in games:
        await update_screenshot_stats(game_id)
    return failed


async def run_repair_job(job_id: int) -> None:
    """Replace Steam's posted time with the capture time across the library."""
    try:
        pending = await _pending_rows()
        if not pending:
            await _set_job(job_id, "completed")
            return
        users = await _steam_users()
        if not users:
            # Nothing says whose profile to ask. Stay queued: the next Steam
            # import records the profile and repairs what it meets.
            return

        await _set_job(job_id, "running")
        api_key = await get_effective_key("steam_api_key")
        failed = 0
        for user_id in users:
            if not pending:
                break
            async with SteamScraper(user_id=user_id, api_key=api_key) as scraper:
                await scraper.validate_profile()
                app_ids = await _apps_of(pending)
                for app_id in app_ids:
                    failed += await _repair_app(scraper, app_id, pending, job_id)
                if pending and not failed:
                    # Merged or renamed games no longer point at the Steam game
                    # their screenshots came from; ask the profile what it has.
                    for game in await scraper.discover_games():
                        if not pending:
                            break
                        if game.app_id not in app_ids:
                            failed += await _repair_app(scraper, game.app_id, pending, job_id)
        if failed:
            raise SteamFetchError(f"Steam did not answer for {failed} screenshot(s)")
        if pending:
            logger.info(
                "%d Steam screenshot(s) still carry the time Steam posted them: Steam did "
                "not list them for the known profile. A Steam import with cookies fixes them.",
                len(pending),
            )
        await _set_job(job_id, "completed")
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.warning("Steam capture time repair did not finish: %s", error)
        db = await get_db()
        await db.rollback()
        # For this job, ``errors`` counts the runs that could not finish.
        await db.execute(
            "UPDATE maintenance_jobs SET errors = errors + 1 WHERE id = ?", (job_id,)
        )
        cursor = await db.execute("SELECT errors FROM maintenance_jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
        attempts = row[0] if row else MAX_ATTEMPTS
        await _set_job(
            job_id, "failed" if attempts >= MAX_ATTEMPTS else "pending", str(error)[:500]
        )
