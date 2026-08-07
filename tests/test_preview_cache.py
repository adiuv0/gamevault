"""Tests for bounded viewer previews and durable cache backfill."""

from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError


def test_preview_width_rejects_values_below_medium_thumbnail():
    from backend.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, preview_max_width=799)


def test_preview_width_rejects_unreasonably_large_values():
    from backend.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, preview_max_width=7681)


@pytest.fixture
async def preview_library(monkeypatch, tmp_path: Path):
    from backend.config import settings
    from backend.database import close_db, get_db, init_db

    await close_db()
    monkeypatch.setattr(settings, "db_path", tmp_path / "preview.db")
    monkeypatch.setattr(settings, "library_dir", tmp_path / "library")
    monkeypatch.setattr(settings, "thumbnail_dir", tmp_path / "cache")
    monkeypatch.setattr(settings, "preview_max_width", 1920)
    await init_db()
    try:
        yield await get_db(), settings
    finally:
        await close_db()


async def _create_asset(db, settings) -> tuple[int, Path]:
    from backend.services.screenshot_service import create_screenshot

    cursor = await db.execute(
        "INSERT INTO games (name, folder_name) VALUES ('Preview Test', 'Preview Test')"
    )
    game_id = cursor.lastrowid
    await db.commit()

    source = settings.library_dir / "Preview Test" / "screenshots" / "wide.png"
    source.parent.mkdir(parents=True)
    Image.new("RGB", (2560, 1440), (20, 80, 140)).save(source)
    screenshot = await create_screenshot(
        game_id=game_id,
        filename=source.name,
        file_path="Preview Test/screenshots/wide.png",
        width=2560,
        height=1440,
        format="png",
    )
    return screenshot["id"], source


@pytest.mark.asyncio
async def test_preview_backfill_is_resumable_and_rebuilds_missing_cache(preview_library):
    from backend.services import preview_service

    db, settings = preview_library
    screenshot_id, _ = await _create_asset(db, settings)

    job = await preview_service.start_backfill_job()
    await preview_service.run_backfill_job(job["id"])
    finished = await preview_service.get_latest_backfill_job()

    assert finished["status"] == "completed"
    assert finished["processed"] == 1
    assert finished["errors"] == 0
    cursor = await db.execute(
        "SELECT preview_path FROM screenshots WHERE id = ?", (screenshot_id,)
    )
    preview_path = (await cursor.fetchone())[0]
    cached = settings.thumbnail_dir / "Preview Test" / "1920" / "wide.jpg"
    assert preview_path == "Preview Test/thumbnails/1920/wide.jpg"
    assert cached.exists()
    with Image.open(cached) as preview:
        assert preview.size == (1920, 1080)

    cached.unlink()
    second_job = await preview_service.start_backfill_job()
    await preview_service.run_backfill_job(second_job["id"])
    assert cached.exists()


@pytest.mark.asyncio
async def test_preview_backfill_records_missing_original(preview_library):
    from backend.services import preview_service

    db, settings = preview_library
    await _create_asset(db, settings)
    (settings.library_dir / "Preview Test" / "screenshots" / "wide.png").unlink()

    job = await preview_service.start_backfill_job()
    await preview_service.run_backfill_job(job["id"])
    finished = await preview_service.get_latest_backfill_job()

    assert finished["status"] == "completed"
    assert finished["processed"] == 1
    assert finished["errors"] == 1


@pytest.mark.asyncio
async def test_preview_endpoint_falls_back_to_medium_thumbnail(preview_library):
    from backend.routers.screenshots import get_preview
    from backend.services.screenshot_service import create_screenshot

    db, settings = preview_library
    cursor = await db.execute(
        "INSERT INTO games (name, folder_name) VALUES ('Fallback', 'Fallback')"
    )
    game_id = cursor.lastrowid
    await db.commit()
    medium = settings.thumbnail_dir / "Fallback" / "800" / "shot.jpg"
    medium.parent.mkdir(parents=True)
    Image.new("RGB", (800, 450), "navy").save(medium)
    screenshot = await create_screenshot(
        game_id=game_id,
        filename="shot.png",
        file_path="Fallback/screenshots/shot.png",
        thumbnail_path_md="Fallback/thumbnails/800/shot.jpg",
        preview_path="Fallback/thumbnails/1920/missing.jpg",
    )

    response = await get_preview(screenshot["id"])
    assert Path(response.path) == medium
    assert response.media_type == "image/jpeg"
