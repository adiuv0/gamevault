"""Tests for encrypted, restart-safe Steam and Special K import jobs."""

import shutil
import time
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture
async def durable_import_db(monkeypatch, tmp_path: Path):
    from backend.config import settings
    from backend.database import close_db, get_db, init_db
    from backend.services.import_job_service import stop_import_worker

    await stop_import_worker()
    await close_db()
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "db_path", tmp_path / "data" / "gamevault.db")
    monkeypatch.setattr(settings, "library_dir", tmp_path / "library")
    monkeypatch.setattr(settings, "thumbnail_dir", tmp_path / "cache")
    monkeypatch.setattr(settings, "secret_key", "durable-import-test-secret")
    await init_db()
    try:
        yield await get_db(), settings
    finally:
        await stop_import_worker()
        await close_db()


@pytest.mark.asyncio
async def test_steam_job_encrypts_payload_and_scrubs_it_after_completion(
    durable_import_db, monkeypatch
):
    from backend.services import import_job_service

    db, _ = durable_import_db
    payload = {
        "user_id": "steam-user",
        "steam_login_secure": "sensitive-cookie",
        "session_id_cookie": "sensitive-session",
        "game_ids": [10, 20],
        "is_numeric_id": False,
    }
    session_id = await import_job_service.create_steam_import_job("steam-user", payload)
    cursor = await db.execute(
        "SELECT status, payload_encrypted FROM import_jobs WHERE session_id = ?",
        (session_id,),
    )
    status, encrypted = await cursor.fetchone()
    assert status == "pending"
    assert "sensitive-cookie" not in encrypted
    assert "sensitive-session" not in encrypted

    captured = {}

    async def fake_dispatch(kind, dispatched_session_id, dispatched_payload):
        captured.update(
            kind=kind,
            session_id=dispatched_session_id,
            payload=dispatched_payload,
        )
        await db.execute(
            "UPDATE steam_import_sessions SET status = 'completed' WHERE id = ?",
            (dispatched_session_id,),
        )
        await db.commit()

    monkeypatch.setattr(import_job_service, "_dispatch_job", fake_dispatch)
    assert await import_job_service.run_next_import_job() is True
    assert captured == {"kind": "steam", "session_id": session_id, "payload": payload}

    cursor = await db.execute(
        """SELECT status, attempts, payload_encrypted, error_message
           FROM import_jobs WHERE session_id = ?""",
        (session_id,),
    )
    job = await cursor.fetchone()
    assert job == ("completed", 1, None, None)


@pytest.mark.asyncio
async def test_interrupted_jobs_return_to_pending_with_session(durable_import_db):
    from backend.services import import_job_service

    db, _ = durable_import_db
    session_id = await import_job_service.create_specialk_import_job(
        "D:/captures",
        {"scan_path_str": "D:/captures", "folder_names": ["Game"]},
    )
    await db.execute(
        "UPDATE import_jobs SET status = 'running', attempts = 1 WHERE session_id = ?",
        (session_id,),
    )
    await db.execute(
        "UPDATE specialk_import_sessions SET status = 'running' WHERE id = ?",
        (session_id,),
    )
    await db.commit()

    assert await import_job_service.recover_interrupted_jobs() == 1
    cursor = await db.execute(
        "SELECT status, attempts, payload_encrypted FROM import_jobs WHERE session_id = ?",
        (session_id,),
    )
    job = await cursor.fetchone()
    assert job[0] == "pending"
    assert job[1] == 1
    assert job[2] is not None
    cursor = await db.execute(
        "SELECT status FROM specialk_import_sessions WHERE id = ?", (session_id,)
    )
    assert (await cursor.fetchone())[0] == "pending"


@pytest.mark.asyncio
async def test_pending_job_can_be_cancelled_without_exposing_payload(durable_import_db):
    from backend.services import import_job_service

    db, _ = durable_import_db
    session_id = await import_job_service.create_steam_import_job(
        "steam-user",
        {"user_id": "steam-user", "steam_login_secure": "secret"},
    )

    assert await import_job_service.cancel_pending_import_job("steam", session_id) is True
    assert await import_job_service.cancel_pending_import_job("steam", session_id) is False

    cursor = await db.execute(
        "SELECT status, payload_encrypted FROM import_jobs WHERE session_id = ?",
        (session_id,),
    )
    assert await cursor.fetchone() == ("cancelled", None)
    cursor = await db.execute(
        "SELECT status, completed_at FROM steam_import_sessions WHERE id = ?",
        (session_id,),
    )
    status, completed_at = await cursor.fetchone()
    assert status == "cancelled"
    assert completed_at is not None


@pytest.mark.asyncio
async def test_specialk_job_replay_does_not_duplicate_screenshot_rows(
    durable_import_db, monkeypatch
):
    from backend.services import import_job_service, specialk_import_service
    from backend.services.game_service import get_or_create_game

    db, settings = durable_import_db
    source_root = settings.data_dir.parent / "specialk"
    source = source_root / "Replay Game" / "Screenshots" / "frame.png"
    source.parent.mkdir(parents=True)
    Image.new("RGB", (640, 360), (40, 100, 160)).save(source)

    # Simulate a crash after game creation and file copy, but before the screenshot row.
    game = await get_or_create_game(name="Replay Game")
    timestamp = time.strftime(
        "%Y%m%d_%H%M%S", time.localtime(source.stat().st_mtime)
    )
    filename = f"specialk_{timestamp}_{source.stem}{source.suffix.lower()}"
    orphan = settings.library_dir / game["folder_name"] / "screenshots" / filename
    orphan.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, orphan)

    async def skip_metadata(_game_id):
        return {}

    monkeypatch.setattr(specialk_import_service, "fetch_and_apply_metadata", skip_metadata)
    payload = {"scan_path_str": str(source_root), "folder_names": ["Replay Game"]}

    first_session = await import_job_service.create_specialk_import_job(
        str(source_root), payload
    )
    assert await import_job_service.run_next_import_job() is True
    second_session = await import_job_service.create_specialk_import_job(
        str(source_root), payload
    )
    assert await import_job_service.run_next_import_job() is True

    cursor = await db.execute("SELECT COUNT(*) FROM screenshots")
    assert (await cursor.fetchone())[0] == 1
    cursor = await db.execute("SELECT filename, file_path FROM screenshots")
    assert await cursor.fetchone() == (
        filename,
        f"{game['folder_name']}/screenshots/{filename}",
    )
    assert [path.name for path in orphan.parent.iterdir()] == [filename]
    cursor = await db.execute(
        """SELECT id, status, completed_screenshots, skipped_screenshots
           FROM specialk_import_sessions WHERE id IN (?, ?) ORDER BY id""",
        (first_session, second_session),
    )
    sessions = await cursor.fetchall()
    assert sessions[0][1:] == ("completed", 1, 0)
    assert sessions[1][1:] == ("completed", 0, 1)


def test_terminal_progress_event_survives_missing_in_memory_queue():
    from backend.services.import_job_service import terminal_progress_event

    assert terminal_progress_event(
        {
            "id": 7,
            "status": "completed",
            "completed_screenshots": 4,
            "skipped_screenshots": 2,
            "failed_screenshots": 1,
            "total_games": 3,
        }
    ) == (
        "import_complete",
        {
            "session_id": 7,
            "completed": 4,
            "skipped": 2,
            "failed": 1,
            "total_games": 3,
        },
    )
    event_type, data = terminal_progress_event(
        {"id": 8, "status": "failed", "error_log": "not-json"}
    )
    assert event_type == "import_error"
    assert data["error"] == "Import failed"


@pytest.mark.asyncio
async def test_durable_import_migration_is_registered(durable_import_db):
    from backend.migrations.runner import MIGRATIONS

    db, _ = durable_import_db
    assert "backend.migrations.m010_durable_import_jobs" in MIGRATIONS
    assert MIGRATIONS.index("backend.migrations.m010_durable_import_jobs") < (
        MIGRATIONS.index("backend.migrations.m011_durable_upload_jobs")
    )
    cursor = await db.execute("PRAGMA table_info(import_jobs)")
    columns = {row[1] for row in await cursor.fetchall()}
    assert {"kind", "session_id", "payload_encrypted", "status", "attempts"} <= columns
