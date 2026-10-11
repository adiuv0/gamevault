"""Durable browser-upload staging and replay tests."""

import asyncio
import importlib
import io
import json
import shutil
from pathlib import Path
from uuid import uuid4

import aiosqlite
import pytest
from PIL import Image


@pytest.fixture
async def durable_upload_db(monkeypatch, tmp_path: Path):
    from backend.config import settings
    from backend.database import close_db, get_db, init_db
    from backend.services.import_job_service import stop_import_worker

    await stop_import_worker()
    await close_db()
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "db_path", tmp_path / "data" / "gamevault.db")
    monkeypatch.setattr(settings, "library_dir", tmp_path / "library")
    monkeypatch.setattr(settings, "thumbnail_dir", tmp_path / "cache")
    monkeypatch.setattr(settings, "secret_key", "durable-upload-test-secret")
    await init_db()
    import aiosqlite
    try:
        async with aiosqlite.connect(str(settings.db_path)) as fixture_db:
            await fixture_db.execute("PRAGMA foreign_keys=ON")
            yield fixture_db, settings
    finally:
        await stop_import_worker()
        await close_db()


async def _create_game(db, name: str = "Upload Replay") -> int:
    cursor = await db.execute(
        "INSERT INTO games (name, folder_name) VALUES (?, ?)",
        (name, name.casefold().replace(" ", "-")),
    )
    await db.commit()
    return cursor.lastrowid


async def _stage_job(db, game_id: int, *, color=(30, 90, 150)) -> tuple[str, Path]:
    from backend.services.upload_job_service import (
        activate_staging_area,
        create_staging_area,
        create_upload_job,
    )

    task_id = str(uuid4())
    partial = create_staging_area(task_id)
    storage_name = f"{uuid4().hex}.bin"
    staged = partial / storage_name
    Image.new("RGB", (96, 54), color).save(staged, format="PNG")
    activate_staging_area(task_id)
    await create_upload_job(
        task_id,
        game_id,
        None,
        [{"display_name": "capture.png", "storage_name": storage_name}],
    )
    return task_id, staged.parent.parent / task_id / storage_name


@pytest.mark.asyncio
async def test_upload_job_registration_uses_persistent_staging(durable_upload_db):
    from backend.services.upload_job_service import get_upload_job, get_upload_staging_root

    db, _ = durable_upload_db
    game_id = await _create_game(db)
    task_id, staged = await _stage_job(db, game_id)

    assert staged.is_file()
    assert staged.is_relative_to(get_upload_staging_root())
    assert not (get_upload_staging_root() / f".{task_id}.partial").exists()

    job = await get_upload_job(task_id)
    assert job is not None
    assert job["status"] == "pending"
    assert job["total_files"] == 1
    assert job["files"] == [
        {
            "file_index": 0,
            "display_name": "capture.png",
            "size_bytes": staged.stat().st_size,
            "status": "pending",
            "screenshot_id": None,
            "error_message": None,
        }
    ]
    cursor = await db.execute("PRAGMA table_info(upload_job_files)")
    columns = {row[1] for row in await cursor.fetchall()}
    assert {"file_index", "storage_name", "status", "target_filename"} <= columns


@pytest.mark.asyncio
async def test_upload_job_migration_preserves_existing_screenshots(tmp_path: Path):
    from backend.migrations.runner import MIGRATIONS

    db = await aiosqlite.connect(tmp_path / "upgrade.db")
    try:
        await db.execute("PRAGMA foreign_keys=ON")
        for migration_name in MIGRATIONS[:-1]:
            module = importlib.import_module(migration_name)
            await module.upgrade(db)

        cursor = await db.execute(
            "INSERT INTO games (name, folder_name) VALUES (?, ?)",
            ("Existing Library", "existing-library"),
        )
        game_id = cursor.lastrowid
        cursor = await db.execute(
            """INSERT INTO screenshots (game_id, filename, file_path, sha256_hash)
               VALUES (?, ?, ?, ?)""",
            (game_id, "kept.png", "existing-library/screenshots/kept.png", "abc123"),
        )
        screenshot_id = cursor.lastrowid
        await db.commit()

        module = importlib.import_module(MIGRATIONS[-1])
        await module.upgrade(db)

        cursor = await db.execute(
            "SELECT game_id, filename, file_path, sha256_hash FROM screenshots WHERE id = ?",
            (screenshot_id,),
        )
        assert await cursor.fetchone() == (
            game_id,
            "kept.png",
            "existing-library/screenshots/kept.png",
            "abc123",
        )
        cursor = await db.execute("PRAGMA foreign_key_list(upload_jobs)")
        assert any(row[2] == "games" and row[6] == "CASCADE" for row in await cursor.fetchall())
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_upload_route_returns_only_after_durable_registration(durable_upload_db):
    from fastapi import UploadFile

    from backend.routers.upload import upload_screenshots
    from backend.services.upload_job_service import get_upload_job, staged_file_path

    db, _ = durable_upload_db
    game_id = await _create_game(db)
    image_bytes = io.BytesIO()
    Image.new("RGB", (48, 27), (110, 70, 30)).save(image_bytes, format="PNG")
    upload = UploadFile(file=io.BytesIO(image_bytes.getvalue()), filename="route.png")

    response = await upload_screenshots(files=[upload], game_id=game_id, taken_at=None)

    job = await get_upload_job(response["task_id"])
    assert job is not None
    assert job["status"] == "pending"
    cursor = await db.execute(
        "SELECT storage_name FROM upload_job_files WHERE task_id = ?",
        (response["task_id"],),
    )
    storage_name = (await cursor.fetchone())[0]
    assert (
        staged_file_path(response["task_id"], storage_name).read_bytes()
        == image_bytes.getvalue()
    )


@pytest.mark.asyncio
async def test_worker_completes_upload_and_cleans_staging(durable_upload_db):
    from backend.services.upload_job_service import get_upload_job, run_next_upload_job

    db, settings = durable_upload_db
    game_id = await _create_game(db)
    task_id, staged = await _stage_job(db, game_id)

    assert await run_next_upload_job() is True

    job = await get_upload_job(task_id)
    assert job is not None
    assert job["status"] == "completed"
    assert job["completed_files"] == 1
    assert job["failed_files"] == 0
    assert not staged.parent.exists()

    cursor = await db.execute(
        "SELECT source_key, sha256_hash FROM screenshots WHERE game_id = ?", (game_id,)
    )
    source_key, sha256_hash = await cursor.fetchone()
    assert source_key == f"upload:{task_id}:0"
    assert len(sha256_hash) == 64
    assert any((settings.library_dir / "upload-replay" / "screenshots").iterdir())


@pytest.mark.asyncio
async def test_terminal_progress_reconnect_replays_snapshot(durable_upload_db):
    from backend.routers.upload import upload_progress
    from backend.services.upload_job_service import run_next_upload_job

    db, _ = durable_upload_db
    game_id = await _create_game(db)
    task_id, _ = await _stage_job(db, game_id)
    assert await run_next_upload_job() is True

    response = await upload_progress(task_id)
    chunks = [chunk async for chunk in response.body_iterator]
    events = [
        json.loads(chunk.removeprefix("data: ").strip())
        for chunk in chunks
    ]
    assert events[0]["type"] == "snapshot"
    assert events[0]["status"] == "completed"
    assert events[0]["files"][0]["status"] == "completed"
    assert events[1] == {"type": "complete"}


@pytest.mark.asyncio
async def test_duplicate_files_in_one_job_create_one_screenshot(durable_upload_db):
    from backend.services.upload_job_service import (
        activate_staging_area,
        create_staging_area,
        create_upload_job,
        get_upload_job,
        run_next_upload_job,
    )

    db, _ = durable_upload_db
    game_id = await _create_game(db)
    task_id = str(uuid4())
    partial = create_staging_area(task_id)
    manifest = []
    for index in range(2):
        storage_name = f"{uuid4().hex}.bin"
        Image.new("RGB", (96, 54), (70, 120, 180)).save(
            partial / storage_name, format="PNG"
        )
        manifest.append(
            {"display_name": f"capture-{index}.png", "storage_name": storage_name}
        )
    activate_staging_area(task_id)
    await create_upload_job(task_id, game_id, None, manifest)

    assert await run_next_upload_job() is True
    cursor = await db.execute("SELECT COUNT(*) FROM screenshots WHERE game_id = ?", (game_id,))
    assert (await cursor.fetchone())[0] == 1
    job = await get_upload_job(task_id)
    assert job is not None
    assert job["completed_files"] == 1
    assert job["skipped_files"] == 1


@pytest.mark.asyncio
async def test_replay_adopts_file_copied_before_screenshot_commit(durable_upload_db):
    from backend.services.upload_job_service import (
        get_upload_job,
        recover_interrupted_upload_jobs,
        reserve_target,
        run_next_upload_job,
    )

    db, settings = durable_upload_db
    game_id = await _create_game(db)
    task_id, staged = await _stage_job(db, game_id)
    target_name = await reserve_target(task_id, 0, "reserved.png")
    target_dir = settings.library_dir / "upload-replay" / "screenshots"
    target_dir.mkdir(parents=True)
    target = target_dir / target_name
    shutil.copy2(staged, target)

    await db.execute("UPDATE upload_jobs SET status = 'running' WHERE task_id = ?", (task_id,))
    await db.execute(
        "UPDATE upload_job_files SET status = 'processing' WHERE task_id = ?", (task_id,)
    )
    await db.commit()

    assert await recover_interrupted_upload_jobs() == 1
    assert await run_next_upload_job() is True
    assert [path.name for path in target_dir.iterdir()] == [target_name]
    cursor = await db.execute("SELECT COUNT(*) FROM screenshots WHERE game_id = ?", (game_id,))
    assert (await cursor.fetchone())[0] == 1
    job = await get_upload_job(task_id)
    assert job is not None
    assert job["status"] == "completed"
    assert job["completed_files"] == 1


@pytest.mark.asyncio
async def test_replay_adopts_screenshot_committed_before_file_status(durable_upload_db):
    from backend.services.screenshot_service import create_screenshot
    from backend.services.upload_job_service import (
        get_upload_job,
        recover_interrupted_upload_jobs,
        run_next_upload_job,
    )

    db, settings = durable_upload_db
    game_id = await _create_game(db)
    task_id, staged = await _stage_job(db, game_id)
    target_dir = settings.library_dir / "upload-replay" / "screenshots"
    target_dir.mkdir(parents=True)
    target = target_dir / "already.png"
    shutil.copy2(staged, target)
    await create_screenshot(
        game_id=game_id,
        filename=target.name,
        file_path="upload-replay/screenshots/already.png",
        source="upload",
        source_key=f"upload:{task_id}:0",
        recalculate_stats=False,
    )
    await db.execute("UPDATE upload_jobs SET status = 'running' WHERE task_id = ?", (task_id,))
    await db.execute(
        "UPDATE upload_job_files SET status = 'processing' WHERE task_id = ?", (task_id,)
    )
    await db.commit()

    assert await recover_interrupted_upload_jobs() == 1
    assert await run_next_upload_job() is True
    cursor = await db.execute("SELECT COUNT(*) FROM screenshots WHERE game_id = ?", (game_id,))
    assert (await cursor.fetchone())[0] == 1
    job = await get_upload_job(task_id)
    assert job is not None
    assert job["completed_files"] == 1
    assert not staged.parent.exists()


@pytest.mark.asyncio
async def test_cancelled_worker_leaves_staging_for_recovery(
    durable_upload_db, monkeypatch
):
    from backend.services import upload_service
    from backend.services.upload_job_service import (
        get_upload_job,
        recover_interrupted_upload_jobs,
        run_next_upload_job,
    )

    db, _ = durable_upload_db
    game_id = await _create_game(db)
    task_id, staged = await _stage_job(db, game_id)

    async def cancel_processing(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(upload_service, "process_upload", cancel_processing)
    with pytest.raises(asyncio.CancelledError):
        await run_next_upload_job()

    assert staged.exists()
    job = await get_upload_job(task_id)
    assert job is not None
    assert job["status"] == "running"
    assert await recover_interrupted_upload_jobs() == 1
    job = await get_upload_job(task_id)
    assert job is not None
    assert job["status"] == "pending"


@pytest.mark.asyncio
async def test_orphan_cleanup_only_removes_owned_task_directories(durable_upload_db):
    from backend.services.upload_job_service import (
        get_upload_staging_root,
        recover_interrupted_upload_jobs,
    )

    _, _settings = durable_upload_db
    root = get_upload_staging_root()
    orphan = root / str(uuid4())
    partial = root / f".{uuid4()}.partial"
    unrelated = root / "keep-me"
    for path in (orphan, partial, unrelated):
        path.mkdir(parents=True, exist_ok=True)

    assert await recover_interrupted_upload_jobs() == 0
    assert not orphan.exists()
    assert not partial.exists()
    assert unrelated.exists()


def test_staged_paths_reject_noncanonical_identifiers():
    from backend.services.upload_job_service import staged_file_path

    with pytest.raises(ValueError, match="task ID"):
        staged_file_path("../outside", f"{uuid4().hex}.bin")
    with pytest.raises(ValueError, match="filename"):
        staged_file_path(str(uuid4()), "../outside.bin")
