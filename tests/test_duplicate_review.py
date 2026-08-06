"""Tests for resumable duplicate scanning and review decisions."""

from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture
async def duplicate_library(monkeypatch, tmp_path: Path):
    from backend.config import settings
    from backend.database import close_db, get_db, init_db

    await close_db()
    monkeypatch.setattr(settings, "db_path", tmp_path / "duplicates.db")
    monkeypatch.setattr(settings, "library_dir", tmp_path / "library")
    monkeypatch.setattr(settings, "thumbnail_dir", tmp_path / "cache")
    await init_db()
    try:
        yield await get_db(), settings.library_dir
    finally:
        await close_db()


async def _create_similar_pair(db, library_dir: Path) -> tuple[int, int, int]:
    from backend.services.screenshot_service import create_screenshot

    cursor = await db.execute(
        "INSERT INTO games (name, folder_name) VALUES ('Review Test', 'Review Test')"
    )
    game_id = cursor.lastrowid
    await db.commit()

    screenshots_dir = library_dir / "Review Test" / "screenshots"
    screenshots_dir.mkdir(parents=True)
    png_path = screenshots_dir / "capture.png"
    jpg_path = screenshots_dir / "capture-copy.jpg"
    image = Image.new("RGB", (640, 360), (30, 90, 160))
    image.save(png_path)
    image.save(jpg_path, quality=92)

    common = {
        "game_id": game_id,
        "width": 640,
        "height": 360,
        "taken_at": "2026-01-02T03:04:05",
        "recalculate_stats": False,
    }
    left = await create_screenshot(
        filename=png_path.name,
        file_path="Review Test/screenshots/capture.png",
        format="png",
        **common,
    )
    right = await create_screenshot(
        filename=jpg_path.name,
        file_path="Review Test/screenshots/capture-copy.jpg",
        format="jpeg",
        **common,
    )
    return game_id, left["id"], right["id"]


@pytest.mark.asyncio
async def test_scan_fingerprints_and_creates_review_candidate(duplicate_library):
    from backend.services import duplicate_service

    db, library_dir = duplicate_library
    _, left_id, right_id = await _create_similar_pair(db, library_dir)

    job = await duplicate_service.start_scan_job()
    await duplicate_service.run_scan_job(job["id"])

    finished = await duplicate_service.get_latest_scan_job()
    assert finished["status"] == "completed"
    assert finished["processed"] == 2
    assert finished["errors"] == 0

    cursor = await db.execute(
        "SELECT id, perceptual_hash FROM screenshots ORDER BY id"
    )
    fingerprints = await cursor.fetchall()
    assert [row[0] for row in fingerprints] == [left_id, right_id]
    assert all(row[1] and len(row[1]) == 32 for row in fingerprints)

    candidates, total = await duplicate_service.list_candidates()
    assert total == 1
    assert candidates[0]["left_screenshot_id"] == left_id
    assert candidates[0]["right_screenshot_id"] == right_id
    assert candidates[0]["confidence"] == "likely"


@pytest.mark.asyncio
async def test_group_candidate_reduces_logical_count(duplicate_library):
    from backend.services import duplicate_service
    from backend.services.game_service import get_game, update_screenshot_stats

    db, library_dir = duplicate_library
    game_id, left_id, right_id = await _create_similar_pair(db, library_dir)
    await update_screenshot_stats(game_id)
    assert (await get_game(game_id))["screenshot_count"] == 2

    job = await duplicate_service.start_scan_job()
    await duplicate_service.run_scan_job(job["id"])
    candidates, _ = await duplicate_service.list_candidates()
    grouped = await duplicate_service.group_candidate(candidates[0]["id"])

    assert grouped["status"] == "grouped"
    cursor = await db.execute(
        "SELECT capture_group_id FROM screenshots WHERE id IN (?, ?) ORDER BY id",
        (left_id, right_id),
    )
    groups = [row[0] for row in await cursor.fetchall()]
    assert groups == [left_id, left_id]
    assert (await get_game(game_id))["screenshot_count"] == 1


@pytest.mark.asyncio
async def test_grouping_resolves_candidates_already_in_same_group(duplicate_library):
    from backend.services import duplicate_service
    from backend.services.screenshot_service import create_screenshot

    db, library_dir = duplicate_library
    game_id, left_id, middle_id = await _create_similar_pair(db, library_dir)
    third_path = library_dir / "Review Test" / "screenshots" / "capture-third.jpg"
    Image.new("RGB", (640, 360), (30, 90, 160)).save(third_path, quality=90)
    third = await create_screenshot(
        game_id=game_id,
        filename=third_path.name,
        file_path="Review Test/screenshots/capture-third.jpg",
        format="jpeg",
        width=640,
        height=360,
        taken_at="2026-01-02T03:04:05",
        recalculate_stats=False,
    )

    job = await duplicate_service.start_scan_job()
    await duplicate_service.run_scan_job(job["id"])
    candidates, total = await duplicate_service.list_candidates()
    assert total == 3
    by_pair = {
        frozenset((item["left_screenshot_id"], item["right_screenshot_id"])): item
        for item in candidates
    }

    await duplicate_service.group_candidate(by_pair[frozenset((left_id, middle_id))]["id"])
    await duplicate_service.group_candidate(by_pair[frozenset((middle_id, third["id"]))]["id"])

    pending, pending_total = await duplicate_service.list_candidates()
    assert pending == []
    assert pending_total == 0
    _, grouped_total = await duplicate_service.list_candidates("grouped")
    assert grouped_total == 3


@pytest.mark.asyncio
async def test_dismiss_candidate_preserves_groups(duplicate_library):
    from backend.services import duplicate_service

    db, library_dir = duplicate_library
    _, left_id, right_id = await _create_similar_pair(db, library_dir)
    job = await duplicate_service.start_scan_job()
    await duplicate_service.run_scan_job(job["id"])
    candidates, _ = await duplicate_service.list_candidates()

    dismissed = await duplicate_service.dismiss_candidate(candidates[0]["id"])
    assert dismissed["status"] == "dismissed"
    cursor = await db.execute(
        "SELECT capture_group_id FROM screenshots WHERE id IN (?, ?) ORDER BY id",
        (left_id, right_id),
    )
    assert [row[0] for row in await cursor.fetchall()] == [left_id, right_id]


@pytest.mark.asyncio
async def test_logical_capture_lists_once_and_shares_metadata(duplicate_library):
    from backend.services import duplicate_service
    from backend.services.screenshot_service import (
        delete_annotation,
        get_annotation,
        list_screenshots,
        save_annotation,
        toggle_favorite,
    )

    db, library_dir = duplicate_library
    game_id, left_id, right_id = await _create_similar_pair(db, library_dir)
    job = await duplicate_service.start_scan_job()
    await duplicate_service.run_scan_job(job["id"])
    candidates, _ = await duplicate_service.list_candidates()
    await duplicate_service.group_candidate(candidates[0]["id"])

    screenshots, total = await list_screenshots(game_id)
    assert total == 1
    assert len(screenshots) == 1
    assert screenshots[0]["variant_count"] == 2
    assert [variant["id"] for variant in screenshots[0]["variants"]] == [
        left_id,
        right_id,
    ]

    assert await toggle_favorite(right_id) is True
    cursor = await db.execute(
        "SELECT is_favorite FROM screenshots WHERE id IN (?, ?) ORDER BY id",
        (left_id, right_id),
    )
    assert [row[0] for row in await cursor.fetchall()] == [1, 1]

    await save_annotation(right_id, "variant note", "<p>variant note</p>")
    assert (await get_annotation(left_id))["content"] == "variant note"
    await db.execute(
        "INSERT INTO annotations (screenshot_id, content) VALUES (?, 'stale note')",
        (left_id,),
    )
    await db.commit()
    await save_annotation(left_id, "capture note", "<p>capture note</p>")
    assert (await get_annotation(right_id))["content"] == "capture note"
    cursor = await db.execute(
        """SELECT COUNT(*) FROM annotations
           WHERE screenshot_id IN (?, ?)""",
        (left_id, right_id),
    )
    assert (await cursor.fetchone())[0] == 1
    assert await delete_annotation(left_id) is True
    assert await get_annotation(right_id) is None


@pytest.mark.asyncio
async def test_scan_records_missing_file_and_continues(duplicate_library):
    from backend.services import duplicate_service
    from backend.services.screenshot_service import create_screenshot

    db, _ = duplicate_library
    cursor = await db.execute(
        "INSERT INTO games (name, folder_name) VALUES ('Missing', 'Missing')"
    )
    game_id = cursor.lastrowid
    await db.commit()
    await create_screenshot(
        game_id=game_id,
        filename="missing.png",
        file_path="Missing/screenshots/missing.png",
        recalculate_stats=False,
    )

    job = await duplicate_service.start_scan_job()
    await duplicate_service.run_scan_job(job["id"])
    finished = await duplicate_service.get_latest_scan_job()
    assert finished["status"] == "completed"
    assert finished["processed"] == 1
    assert finished["errors"] == 1
