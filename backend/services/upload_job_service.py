"""SQLite-backed jobs and durable staging for browser uploads."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID

from backend.config import settings
from backend.database import get_db

logger = logging.getLogger(__name__)

_STORAGE_NAME_PATTERN = re.compile(r"^[0-9a-f]{32}\.bin$")
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def get_upload_staging_root() -> Path:
    """Return the persistent staging root under GameVault's data directory."""
    return settings.data_dir / "upload-staging"


def _canonical_task_id(task_id: str) -> str:
    try:
        canonical = str(UUID(task_id))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Invalid upload task ID") from error
    if canonical != task_id:
        raise ValueError("Invalid upload task ID")
    return canonical


def _task_dir(task_id: str, *, partial: bool = False) -> Path:
    canonical = _canonical_task_id(task_id)
    name = f".{canonical}.partial" if partial else canonical
    return get_upload_staging_root() / name


def create_staging_area(task_id: str) -> Path:
    root = get_upload_staging_root()
    root.mkdir(parents=True, exist_ok=True)
    partial = _task_dir(task_id, partial=True)
    partial.mkdir()
    return partial


def activate_staging_area(task_id: str) -> Path:
    """Atomically make a fully received upload visible to the durable worker."""
    partial = _task_dir(task_id, partial=True)
    final = _task_dir(task_id)
    if not partial.is_dir():
        raise FileNotFoundError(partial)
    if final.exists():
        raise FileExistsError(final)
    partial.replace(final)
    return final


def cleanup_staging_area(task_id: str) -> None:
    """Remove only canonical task directories owned by the upload staging root."""
    for path in (_task_dir(task_id), _task_dir(task_id, partial=True)):
        shutil.rmtree(path, ignore_errors=True)


def staged_file_path(task_id: str, storage_name: str) -> Path:
    if not _STORAGE_NAME_PATTERN.fullmatch(storage_name):
        raise ValueError("Invalid staged upload filename")
    return _task_dir(task_id) / storage_name


async def create_upload_job(
    task_id: str,
    game_id: int,
    taken_at: str | None,
    files: list[dict[str, Any]],
) -> None:
    """Register an activated staging directory and wake the shared worker."""
    _canonical_task_id(task_id)
    if not files:
        raise ValueError("Upload job must contain at least one file")
    if not _task_dir(task_id).is_dir():
        raise FileNotFoundError(_task_dir(task_id))

    normalized_files = []
    for index, file in enumerate(files):
        storage_name = file["storage_name"]
        staged = staged_file_path(task_id, storage_name)
        if not staged.is_file():
            raise FileNotFoundError(staged)
        normalized_files.append(
            (
                task_id,
                index,
                file["display_name"],
                storage_name,
                staged.stat().st_size,
            )
        )

    db = await get_db()
    try:
        from backend.services.game_service import resolve_game_id
        await db.execute("BEGIN IMMEDIATE")
        game_id = await resolve_game_id(game_id)
        await db.execute(
            """INSERT INTO upload_jobs (task_id, game_id, taken_at, total_files)
               VALUES (?, ?, ?, ?)""",
            (task_id, game_id, taken_at, len(normalized_files)),
        )
        await db.executemany(
            """INSERT INTO upload_job_files (
                   task_id, file_index, display_name, storage_name, size_bytes
               ) VALUES (?, ?, ?, ?, ?)""",
            normalized_files,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    from backend.services.import_job_service import wake_import_worker

    wake_import_worker()


async def get_upload_job(task_id: str) -> dict[str, Any] | None:
    _canonical_task_id(task_id)
    db = await get_db()
    cursor = await db.execute("SELECT * FROM upload_jobs WHERE task_id = ?", (task_id,))
    row = await cursor.fetchone()
    if row is None:
        return None
    job = dict(zip((column[0] for column in cursor.description), row))
    job["files"] = await _get_job_files(task_id, public=True)
    return job


async def _get_job_files(task_id: str, *, public: bool) -> list[dict[str, Any]]:
    db = await get_db()
    columns = (
        "file_index, display_name, size_bytes, status, screenshot_id, error_message"
        if public
        else "file_index, display_name, storage_name, size_bytes, status, target_filename"
    )
    cursor = await db.execute(
        f"SELECT {columns} FROM upload_job_files WHERE task_id = ? ORDER BY file_index",
        (task_id,),
    )
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in await cursor.fetchall()]


def upload_snapshot_event(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "snapshot",
        "task_id": job["task_id"],
        "game_id": job["game_id"],
        "status": job["status"],
        "total_files": job["total_files"],
        "completed_files": job["completed_files"],
        "skipped_files": job["skipped_files"],
        "failed_files": job["failed_files"],
        "error": job.get("error_message"),
        "files": job["files"],
    }


def is_terminal_upload(job: dict[str, Any]) -> bool:
    return job["status"] in _TERMINAL_STATUSES


async def record_upload_event(task_id: str, event: dict[str, Any]) -> None:
    """Persist per-file progress before exposing the event to SSE clients."""
    file_index = event.get("file_index")
    if not isinstance(file_index, int):
        return

    event_type = event.get("type")
    db = await get_db()
    if event_type == "file_start":
        await db.execute(
            """UPDATE upload_job_files
               SET status = 'processing', error_message = NULL,
                   updated_at = datetime('now')
               WHERE task_id = ? AND file_index = ?
                 AND status IN ('pending', 'processing')""",
            (task_id, file_index),
        )
    elif event_type == "file_complete":
        await db.execute(
            """UPDATE upload_job_files
               SET status = 'completed', screenshot_id = ?, error_message = NULL,
                   updated_at = datetime('now')
               WHERE task_id = ? AND file_index = ?""",
            (event.get("screenshot_id"), task_id, file_index),
        )
    elif event_type == "file_skipped":
        await db.execute(
            """UPDATE upload_job_files
               SET status = 'skipped', error_message = ?, updated_at = datetime('now')
               WHERE task_id = ? AND file_index = ?""",
            (event.get("reason"), task_id, file_index),
        )
    elif event_type == "file_error":
        await db.execute(
            """UPDATE upload_job_files
               SET status = 'failed', error_message = ?, updated_at = datetime('now')
               WHERE task_id = ? AND file_index = ?""",
            (event.get("error") or "Upload failed", task_id, file_index),
        )
    else:
        return

    await _refresh_counts(db, task_id)
    await db.commit()


async def get_reserved_target(task_id: str, file_index: int) -> str | None:
    db = await get_db()
    cursor = await db.execute(
        """SELECT target_filename FROM upload_job_files
           WHERE task_id = ? AND file_index = ?""",
        (task_id, file_index),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ValueError("Upload job file not found")
    target = row[0]
    if target is not None and (Path(target).name != target or target in {".", ".."}):
        raise ValueError("Invalid reserved upload filename")
    return target


async def reserve_target(task_id: str, file_index: int, filename: str) -> str:
    if Path(filename).name != filename or filename in {".", ".."}:
        raise ValueError("Invalid upload target filename")
    db = await get_db()
    await db.execute(
        """UPDATE upload_job_files
           SET target_filename = ?, updated_at = datetime('now')
           WHERE task_id = ? AND file_index = ? AND target_filename IS NULL""",
        (filename, task_id, file_index),
    )
    await db.commit()
    target = await get_reserved_target(task_id, file_index)
    if target is None:
        raise ValueError("Could not reserve upload target filename")
    return target


async def recover_interrupted_upload_jobs() -> int:
    """Requeue interrupted jobs and remove staging directories with no active job."""
    from backend.services.import_job_service import MAX_JOB_ATTEMPTS

    db = await get_db()
    cursor = await db.execute(
        "SELECT task_id, attempts FROM upload_jobs WHERE status = 'running'"
    )
    rows = await cursor.fetchall()
    interrupted = [task_id for task_id, _ in rows]
    if interrupted:
        # A file that was mid-processing through repeated crashes is the likely
        # cause; fail it so the rest of the batch can finish instead of looping.
        exhausted = [task_id for task_id, attempts in rows if attempts >= MAX_JOB_ATTEMPTS]
        if exhausted:
            logger.error("Failing files that interrupted upload jobs %s repeatedly", exhausted)
            await db.execute(
                f"""UPDATE upload_job_files
                    SET status = 'failed', error_message = ?, updated_at = datetime('now')
                    WHERE task_id IN ({", ".join("?" for _ in exhausted)})
                      AND status = 'processing'""",
                ("Processing this file was interrupted repeatedly; it was skipped", *exhausted),
            )
            for task_id in exhausted:
                await _refresh_counts(db, task_id)
        placeholders = ", ".join("?" for _ in interrupted)
        await db.execute(
            f"""UPDATE upload_job_files SET status = 'pending', updated_at = datetime('now')
                WHERE task_id IN ({placeholders}) AND status = 'processing'""",
            interrupted,
        )
        await db.execute(
            f"""UPDATE upload_jobs
                SET status = 'pending', started_at = NULL, updated_at = datetime('now')
                WHERE task_id IN ({placeholders})""",
            interrupted,
        )
        await db.commit()

    cursor = await db.execute(
        "SELECT task_id FROM upload_jobs WHERE status IN ('pending', 'running')"
    )
    active = {row[0] for row in await cursor.fetchall()}
    _cleanup_orphan_staging(active)
    if interrupted:
        logger.warning("Recovered %d interrupted upload job(s)", len(interrupted))
    return len(interrupted)


def _cleanup_orphan_staging(active_task_ids: set[str]) -> None:
    root = get_upload_staging_root()
    root.mkdir(parents=True, exist_ok=True)
    for path in root.iterdir():
        name = path.name
        candidate = name[1:-8] if name.startswith(".") and name.endswith(".partial") else name
        try:
            canonical = _canonical_task_id(candidate)
        except ValueError:
            continue
        if name.startswith(".") or canonical not in active_task_ids:
            shutil.rmtree(path, ignore_errors=True)


async def run_next_upload_job() -> bool:
    job = await _claim_next_upload_job()
    if job is None:
        return False
    await _run_claimed_upload_job(job)
    return True


async def _claim_next_upload_job() -> dict[str, Any] | None:
    db = await get_db()
    cursor = await db.execute(
        """SELECT task_id, game_id, taken_at FROM upload_jobs
           WHERE status = 'pending' ORDER BY created_at, task_id LIMIT 1"""
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    task_id, game_id, taken_at = row
    cursor = await db.execute(
        """UPDATE upload_jobs
           SET status = 'running', attempts = attempts + 1,
               started_at = datetime('now'), updated_at = datetime('now')
           WHERE task_id = ? AND status = 'pending'""",
        (task_id,),
    )
    await db.commit()
    if cursor.rowcount != 1:
        return None
    return {"task_id": task_id, "game_id": game_id, "taken_at": taken_at}


async def _run_claimed_upload_job(job: dict[str, Any]) -> None:
    task_id = job["task_id"]
    try:
        files = await _get_job_files(task_id, public=False)
        active_files = [
            (
                file["file_index"],
                file["display_name"],
                staged_file_path(task_id, file["storage_name"]),
            )
            for file in files
            if file["status"] in {"pending", "processing"}
        ]

        from backend.services.upload_service import emit_progress, process_upload

        await process_upload(
            task_id,
            job["game_id"],
            active_files,
            job["taken_at"],
            durable=True,
            emit_complete=False,
        )
        await _finish_upload_job(task_id, "completed")
        finished = await get_upload_job(task_id)
        assert finished is not None
        await emit_progress(
            task_id,
            {
                "type": "complete",
                "total_processed": finished["completed_files"],
                "total_skipped": finished["skipped_files"],
                "total_failed": finished["failed_files"],
                "total_files": finished["total_files"],
            },
        )
        await asyncio.to_thread(cleanup_staging_area, task_id)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.exception("Durable upload job %s failed", task_id)
        await _finish_upload_job(task_id, "failed", str(error))
        from backend.services.upload_service import emit_progress

        await emit_progress(task_id, {"type": "error", "error": str(error)})
        await emit_progress(task_id, {"type": "complete"})
        await asyncio.to_thread(cleanup_staging_area, task_id)


async def _finish_upload_job(
    task_id: str,
    status: str,
    error_message: str | None = None,
) -> None:
    db = await get_db()
    if status == "failed":
        await db.execute(
            """UPDATE upload_job_files
               SET status = 'failed', error_message = COALESCE(error_message, ?),
                   updated_at = datetime('now')
               WHERE task_id = ? AND status IN ('pending', 'processing')""",
            (error_message or "Upload failed", task_id),
        )
    await _refresh_counts(db, task_id)
    await db.execute(
        """UPDATE upload_jobs
           SET status = ?, error_message = ?, completed_at = datetime('now'),
               updated_at = datetime('now') WHERE task_id = ?""",
        (status, error_message, task_id),
    )
    await db.commit()


async def _refresh_counts(db: Any, task_id: str) -> None:
    await db.execute(
        """UPDATE upload_jobs SET
               completed_files = (
                   SELECT COUNT(*) FROM upload_job_files
                   WHERE task_id = ? AND status = 'completed'
               ),
               skipped_files = (
                   SELECT COUNT(*) FROM upload_job_files
                   WHERE task_id = ? AND status = 'skipped'
               ),
               failed_files = (
                   SELECT COUNT(*) FROM upload_job_files
                   WHERE task_id = ? AND status = 'failed'
               ),
               updated_at = datetime('now')
           WHERE task_id = ?""",
        (task_id, task_id, task_id, task_id),
    )
