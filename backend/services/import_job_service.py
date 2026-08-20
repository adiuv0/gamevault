"""Encrypted SQLite-backed worker for restart-safe filesystem imports."""

from __future__ import annotations

import asyncio
import base64
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

_worker_task: asyncio.Task | None = None
_worker_wakeup: asyncio.Event | None = None


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

    if _worker_wakeup is not None:
        _worker_wakeup.set()
    return session_id


async def recover_interrupted_jobs() -> int:
    """Return interrupted jobs to pending so idempotent import code can replay them."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT kind, session_id FROM import_jobs WHERE status = 'running'"
    )
    interrupted = await cursor.fetchall()
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


async def _mark_session_failed(kind: str, session_id: int, message: str) -> None:
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


async def _worker_loop() -> None:
    assert _worker_wakeup is not None
    while True:
        if await run_next_import_job():
            continue
        _worker_wakeup.clear()
        try:
            await asyncio.wait_for(_worker_wakeup.wait(), timeout=_POLL_SECONDS)
        except TimeoutError:
            pass


async def start_import_worker() -> asyncio.Task:
    """Recover interrupted work and start the single in-process queue worker."""
    global _worker_task, _worker_wakeup
    if _worker_task is not None and not _worker_task.done():
        return _worker_task
    _worker_wakeup = asyncio.Event()
    await recover_interrupted_jobs()
    _worker_task = asyncio.create_task(_worker_loop(), name="gamevault-import-worker")
    return _worker_task


async def stop_import_worker() -> None:
    """Stop the worker; a running job remains recoverable on next startup."""
    global _worker_task, _worker_wakeup
    if _worker_task is not None:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
    _worker_task = None
    _worker_wakeup = None
