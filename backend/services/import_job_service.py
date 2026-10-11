"""Encrypted SQLite-backed worker for restart-safe filesystem imports."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.config import settings
from backend.database import get_db

logger = logging.getLogger(__name__)

_PAYLOAD_AAD = b"gamevault-import-job-v1"
_POLL_SECONDS = 1.0
_ERROR_BACKOFF_SECONDS = 5.0
# A job interrupted this many times (each one a process crash or restart while it
# ran) is failed instead of requeued, so one bad input cannot crash-loop the server.
MAX_JOB_ATTEMPTS = 3

# Uploads and imports run in separate lanes so a multi-hour import never queues
# browser uploads behind it.
_worker_tasks: list[asyncio.Task] = []
_worker_wakeups: list[asyncio.Event] = []


def _payload_key() -> bytes:
    return hashlib.sha256(settings.secret_key.encode("utf-8")).digest()


def _encrypt_payload(payload: dict[str, Any]) -> str:
    nonce = os.urandom(12)
    plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(_payload_key()).encrypt(nonce, plaintext, _PAYLOAD_AAD)
    return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def _decrypt_payload(encoded: str) -> dict[str, Any]:
    raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
    if len(raw) < 29:
        raise ValueError("Invalid encrypted import payload")
    plaintext = AESGCM(_payload_key()).decrypt(raw[:12], raw[12:], _PAYLOAD_AAD)
    payload = json.loads(plaintext)
    if not isinstance(payload, dict):
        raise TypeError("Invalid import payload shape")
    return payload


async def create_steam_import_job(steam_user_id: str, payload: dict[str, Any]) -> int:
    return await _create_session_job(
        kind="steam",
        session_table="steam_import_sessions",
        session_column="steam_user_id",
        session_value=steam_user_id,
        payload=payload,
    )


async def create_specialk_import_job(scan_path: str, payload: dict[str, Any]) -> int:
    return await _create_session_job(
        kind="specialk",
        session_table="specialk_import_sessions",
        session_column="scan_path",
        session_value=scan_path,
        payload=payload,
    )


async def _create_session_job(
    *,
    kind: str,
    session_table: str,
    session_column: str,
    session_value: str,
    payload: dict[str, Any],
) -> int:
    """Create the import session and its queued work in one transaction."""
    db = await get_db()
    try:
        cursor = await db.execute(
            f"INSERT INTO {session_table} ({session_column}, status) VALUES (?, 'pending')",
            (session_value,),
        )
        session_id = cursor.lastrowid
        await db.execute(
            """INSERT INTO import_jobs (kind, session_id, payload_encrypted)
               VALUES (?, ?, ?)""",
            (kind, session_id, _encrypt_payload(payload)),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    wake_import_worker()
    return session_id


def wake_import_worker() -> None:
    """Wake the durable-work lanes after any job type is queued."""
    for wakeup in _worker_wakeups:
        wakeup.set()


async def recover_interrupted_jobs() -> int:
    """Return interrupted jobs to pending so idempotent import code can replay them."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, kind, session_id, attempts FROM import_jobs WHERE status = 'running'"
    )
    interrupted = await cursor.fetchall()
    exhausted = [row for row in interrupted if row[3] >= MAX_JOB_ATTEMPTS]
    for job_id, kind, session_id, attempts in exhausted:
        message = (
            f"Import was interrupted {attempts} times and will not be retried "
            "automatically. Check the server logs, then start the import again."
        )
        logger.error("Import job %s exceeded %d attempts; marking failed", job_id, attempts)
        await _mark_session_failed(kind, session_id, message, commit=False)
        await db.execute(
            """UPDATE import_jobs
               SET status = 'failed', payload_encrypted = NULL, error_message = ?,
                   completed_at = datetime('now'), updated_at = datetime('now')
               WHERE id = ?""",
            (message, job_id),
        )
    interrupted = [row[1:3] for row in interrupted if row[3] < MAX_JOB_ATTEMPTS]
    for kind, session_id in interrupted:
        table = _session_table(kind)
        await db.execute(
            f"""UPDATE {table}
                SET status = 'pending', completed_at = NULL
                WHERE id = ? AND status = 'running'""",
            (session_id,),
        )
    await db.execute(
        """UPDATE import_jobs
           SET status = 'pending', started_at = NULL, updated_at = datetime('now')
           WHERE status = 'running'"""
    )
    await db.commit()
    if interrupted:
        logger.warning("Recovered %d interrupted import job(s)", len(interrupted))
    return len(interrupted)


async def cancel_pending_import_job(kind: str, session_id: int) -> bool:
    """Cancel a queued job before the worker claims it."""
    db = await get_db()
    cursor = await db.execute(
        """UPDATE import_jobs
           SET status = 'cancelled', payload_encrypted = NULL,
               completed_at = datetime('now'), updated_at = datetime('now')
           WHERE kind = ? AND session_id = ? AND status = 'pending'""",
        (kind, session_id),
    )
    if cursor.rowcount != 1:
        await db.rollback()
        return False
    await db.execute(
        f"""UPDATE {_session_table(kind)}
            SET status = 'cancelled', completed_at = datetime('now')
            WHERE id = ? AND status = 'pending'""",
        (session_id,),
    )
    await db.commit()
    return True


async def run_next_import_job() -> bool:
    """Claim and run one queued import. Returns whether a job was found."""
    job = await _claim_next_job()
    if job is None:
        return False

    try:
        payload = _decrypt_payload(job["payload_encrypted"])
        await _dispatch_job(job["kind"], job["session_id"], payload)
        session_status = await _get_session_status(job["kind"], job["session_id"])
        terminal_status = (
            session_status
            if session_status in {"completed", "failed", "cancelled"}
            else "failed"
        )
        if terminal_status == "failed" and session_status != "failed":
            await _mark_session_failed(
                job["kind"], job["session_id"], "Import worker exited without a terminal status"
            )
        await _finish_job(job["id"], terminal_status)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.exception("Durable %s import job %s failed", job["kind"], job["id"])
        message = f"Durable import worker error: {error}"
        await _mark_session_failed(job["kind"], job["session_id"], message)
        await _finish_job(job["id"], "failed", message)
    return True


async def _claim_next_job() -> dict[str, Any] | None:
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, kind, session_id, payload_encrypted
           FROM import_jobs WHERE status = 'pending' ORDER BY id LIMIT 1"""
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    job_id, kind, session_id, payload_encrypted = row
    cursor = await db.execute(
        """UPDATE import_jobs
           SET status = 'running', attempts = attempts + 1,
               started_at = datetime('now'), updated_at = datetime('now')
           WHERE id = ? AND status = 'pending'""",
        (job_id,),
    )
    await db.commit()
    if cursor.rowcount != 1:
        return None
    return {
        "id": job_id,
        "kind": kind,
        "session_id": session_id,
        "payload_encrypted": payload_encrypted,
    }


async def _dispatch_job(kind: str, session_id: int, payload: dict[str, Any]) -> None:
    if kind == "steam":
        from backend.services.steam_import_service import run_import

        await run_import(session_id=session_id, **payload)
        return
    if kind == "specialk":
        from backend.services.specialk_import_service import run_import

        await run_import(session_id=session_id, **payload)
        return
    raise ValueError(f"Unknown import job kind: {kind}")


async def _get_session_status(kind: str, session_id: int) -> str | None:
    db = await get_db()
    cursor = await db.execute(
        f"SELECT status FROM {_session_table(kind)} WHERE id = ?", (session_id,)
    )
    row = await cursor.fetchone()
    return row[0] if row else None


async def _mark_session_failed(
    kind: str, session_id: int, message: str, *, commit: bool = True
) -> None:
    db = await get_db()
    table = _session_table(kind)
    cursor = await db.execute(f"SELECT error_log FROM {table} WHERE id = ?", (session_id,))
    row = await cursor.fetchone()
    errors = _parse_error_log(row[0] if row else None)
    errors.append(message)
    await db.execute(
        f"""UPDATE {table}
            SET status = 'failed', error_log = ?, completed_at = datetime('now')
            WHERE id = ?""",
        (json.dumps(errors), session_id),
    )
    if commit:
        await db.commit()


async def _finish_job(job_id: int, status: str, error_message: str | None = None) -> None:
    db = await get_db()
    await db.execute(
        """UPDATE import_jobs
           SET status = ?, payload_encrypted = NULL, error_message = ?,
               completed_at = datetime('now'), updated_at = datetime('now')
           WHERE id = ?""",
        (status, error_message, job_id),
    )
    await db.commit()


def _session_table(kind: str) -> str:
    if kind == "steam":
        return "steam_import_sessions"
    if kind == "specialk":
        return "specialk_import_sessions"
    raise ValueError(f"Unknown import job kind: {kind}")


def _parse_error_log(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def terminal_progress_event(session: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Build a terminal SSE event when an in-memory queue did not survive a restart."""
    status = session.get("status")
    if status not in {"completed", "failed", "cancelled"}:
        return None
    event_type = {
        "completed": "import_complete",
        "failed": "import_error",
        "cancelled": "import_cancelled",
    }[status]
    data: dict[str, Any] = {
        "session_id": session["id"],
        "completed": session.get("completed_screenshots", 0),
        "skipped": session.get("skipped_screenshots", 0),
        "failed": session.get("failed_screenshots", 0),
        "total_games": session.get("total_games", 0),
    }
    if status == "failed":
        errors = _parse_error_log(session.get("error_log"))
        data["error"] = errors[-1] if errors else "Import failed"
    return event_type, data


async def _lane_loop(name: str, run_next, wakeup: asyncio.Event) -> None:
    """Run one queue forever; an unexpected error is logged and retried, never fatal."""
    while True:
        try:
            if await run_next():
                continue
        except asyncio.CancelledError:
            raise
        except Exception:
            # Without this guard one transient error (for example "database is
            # locked") silently stopped all background work until a restart.
            logger.exception("Durable %s worker error; retrying", name)
            await asyncio.sleep(_ERROR_BACKOFF_SECONDS)
            continue
        wakeup.clear()
        try:
            await asyncio.wait_for(wakeup.wait(), timeout=_POLL_SECONDS)
        except TimeoutError:
            pass


def _log_lane_exit(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("Durable worker %s stopped", task.get_name(), exc_info=task.exception())


async def start_import_worker() -> list[asyncio.Task]:
    """Recover interrupted work and start the upload and import worker lanes."""
    if _worker_tasks and not any(task.done() for task in _worker_tasks):
        return _worker_tasks
    await stop_import_worker()
    await recover_interrupted_jobs()
    from backend.services.upload_job_service import (
        recover_interrupted_upload_jobs,
        run_next_upload_job,
    )

    await recover_interrupted_upload_jobs()
    for name, run_next in (("upload", run_next_upload_job), ("import", run_next_import_job)):
        wakeup = asyncio.Event()
        task = asyncio.create_task(
            _lane_loop(name, run_next, wakeup), name=f"gamevault-{name}-worker"
        )
        task.add_done_callback(_log_lane_exit)
        _worker_wakeups.append(wakeup)
        _worker_tasks.append(task)
    return _worker_tasks


async def stop_import_worker() -> None:
    """Stop the workers; a running job remains recoverable on next startup."""
    tasks = list(_worker_tasks)
    _worker_tasks.clear()
    _worker_wakeups.clear()
    for task in tasks:
        task.cancel()
    for task in tasks:
        # Lane failures were already logged by _log_lane_exit.
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
