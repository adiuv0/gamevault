"""Tests for resumable duplicate scanning and review decisions."""

from pathlib import Path

import aiosqlite
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
    import aiosqlite
    try:
        async with aiosqlite.connect(str(settings.db_path)) as fixture_db:
            await fixture_db.execute("PRAGMA foreign_keys=ON")
            yield fixture_db, settings.library_dir
    finally:
        await close_db()


@pytest.fixture
def review_only(monkeypatch):
    """Turn off automatic pairing to exercise the manual review queue on its own."""
    from backend.services import capture_matching

    monkeypatch.setattr(capture_matching, "VARIANT_HASH_DISTANCE", -1)


async def _create_similar_pair(
    db, library_dir: Path, *, right_is_hdr: bool = True
) -> tuple[int, int, int]:
    from backend.services.screenshot_service import create_screenshot

    cursor = await db.execute(
        "INSERT INTO games (name, folder_name) VALUES ('Review Test', 'Review Test')"
    )
    game_id = cursor.lastrowid
    await db.commit()

    screenshots_dir = library_dir / "Review Test" / "screenshots"
    screenshots_dir.mkdir(parents=True)
    png_path = screenshots_dir / "capture.png"
    right_path = screenshots_dir / (
        "capture-hdr.jxr" if right_is_hdr else "capture-copy.jpg"
    )
    image = Image.new("RGB", (640, 360), (30, 90, 160))
    image.save(png_path)
    if right_is_hdr:
        image.save(right_path, format="PNG")
    else:
        image.save(right_path, quality=92)

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
        filename=right_path.name,
        file_path=f"Review Test/screenshots/{right_path.name}",
        format="jxr" if right_is_hdr else "jpeg",
        **common,
    )
    return game_id, left["id"], right["id"]


@pytest.mark.asyncio
async def test_hdr_classification_migration_backfills_known_formats(tmp_path: Path):
    from backend.migrations.m012_hdr_classification import upgrade

    db = await aiosqlite.connect(tmp_path / "hdr-classification.db")
    try:
        await db.execute(
            """CREATE TABLE screenshots (
                   id INTEGER PRIMARY KEY,
                   filename TEXT NOT NULL,
                   format TEXT
               )"""
        )
        await db.executemany(
            "INSERT INTO screenshots (id, filename, format) VALUES (?, ?, ?)",
            [
                (1, "ordinary.png", "png"),
                (2, "capture.jxr", "jxr"),
                (3, "capture.wdp", None),
            ],
        )
        await upgrade(db)
        cursor = await db.execute("SELECT id, is_hdr FROM screenshots ORDER BY id")
        assert await cursor.fetchall() == [(1, None), (2, 1), (3, 1)]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_scan_fingerprints_and_creates_review_candidate(duplicate_library, review_only):
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
        "SELECT id, perceptual_hash, is_hdr FROM screenshots ORDER BY id"
    )
    fingerprints = await cursor.fetchall()
    assert [row[0] for row in fingerprints] == [left_id, right_id]
    assert all(row[1] and len(row[1]) == 32 for row in fingerprints)
    assert [row[2] for row in fingerprints] == [0, 1]

    candidates, total = await duplicate_service.list_candidates()
    assert total == 1
    assert candidates[0]["left_screenshot_id"] == left_id
    assert candidates[0]["right_screenshot_id"] == right_id
    assert candidates[0]["confidence"] == "likely"
    assert candidates[0]["reason"] == "hdr_sdr_capture_time"


@pytest.mark.asyncio
async def test_scan_groups_png_and_jpg_copies_of_one_capture(duplicate_library):
    from backend.services import duplicate_service

    db, library_dir = duplicate_library
    _, left_id, right_id = await _create_similar_pair(
        db, library_dir, right_is_hdr=False
    )
    job = await duplicate_service.start_scan_job()
    await duplicate_service.run_scan_job(job["id"])

    cursor = await db.execute(
        "SELECT capture_group_id FROM screenshots WHERE id IN (?, ?) ORDER BY id",
        (left_id, right_id),
    )
    assert [row[0] for row in await cursor.fetchall()] == [left_id, left_id]
    candidates, total = await duplicate_service.list_candidates()
    assert candidates == []
    assert total == 0


@pytest.mark.asyncio
async def test_png_jpg_and_jxr_of_one_moment_become_one_capture(duplicate_library):
    from backend.services.screenshot_service import create_screenshot

    db, _ = duplicate_library
    cursor = await db.execute(
        "INSERT INTO games (name, folder_name) VALUES ('Auto Match', 'Auto Match')"
    )
    game_id = cursor.lastrowid
    await db.commit()
    common = {
        "game_id": game_id,
        "width": 640,
        "height": 360,
        "taken_at": "2026-01-02T03:04:05",
        "perceptual_hash": "0" * 32,
        "recalculate_stats": False,
    }
    first = await create_screenshot(
        filename="first.png",
        file_path="Auto Match/first.png",
        format="png",
        is_hdr=False,
        **common,
    )
    second = await create_screenshot(
        filename="second.jpg",
        file_path="Auto Match/second.jpg",
        format="jpeg",
        is_hdr=False,
        **common,
    )
    hdr = await create_screenshot(
        filename="third.jxr",
        file_path="Auto Match/third.jxr",
        format="jxr",
        is_hdr=True,
        **common,
    )

    cursor = await db.execute(
        "SELECT id, capture_group_id FROM screenshots WHERE game_id = ? ORDER BY id",
        (game_id,),
    )
    assert await cursor.fetchall() == [
        (first["id"], first["id"]),
        (second["id"], first["id"]),
        (hdr["id"], first["id"]),
    ]


@pytest.mark.asyncio
async def test_group_candidate_reduces_logical_count(duplicate_library, review_only):
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
async def test_grouping_resolves_candidates_already_in_same_group(duplicate_library, review_only):
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
    assert total == 2
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
    assert grouped_total == 2


@pytest.mark.asyncio
async def test_dismiss_candidate_preserves_groups(duplicate_library, review_only):
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
async def test_logical_capture_lists_once_and_shares_metadata(duplicate_library, review_only):
    from backend.services import duplicate_service
    from backend.services.screenshot_service import (
        delete_annotation,
        get_annotation,
        get_capture_details,
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
    assert len(screenshots[0]["media_version"]) == 16
    assert "variants" not in screenshots[0]

    capture = await get_capture_details(right_id)
    assert capture["id"] == left_id
    assert capture["variant_count"] == 2
    assert all(len(variant["media_version"]) == 16 for variant in capture["variants"])
    assert [variant["id"] for variant in capture["variants"]] == [
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


async def _create_named_pair(db, *, hdr_name: str, sdr_name: str = "shot.png",
                             hdr_hash: str = "0" * 32, hdr_taken_at: str = "2026-01-02T03:04:05",
                             hdr_width: int = 640,
                             sdr_taken_at: str | None = "2026-01-02T03:04:05") -> tuple[dict, dict]:
    from backend.services.screenshot_service import create_screenshot

    cursor = await db.execute(
        "INSERT INTO games (name, folder_name) VALUES ('Pairs', 'Pairs')"
    )
    game_id = cursor.lastrowid
    await db.commit()
    sdr = await create_screenshot(
        game_id=game_id, filename=sdr_name, file_path=f"Pairs/{sdr_name}",
        format="png", is_hdr=False, width=640, height=360,
        taken_at=sdr_taken_at, perceptual_hash="0" * 32,
        recalculate_stats=False,
    )
    hdr = await create_screenshot(
        game_id=game_id, filename=hdr_name, file_path=f"Pairs/{hdr_name}",
        format="jxr", is_hdr=True, width=hdr_width, height=360,
        taken_at=hdr_taken_at, perceptual_hash=hdr_hash,
        recalculate_stats=False,
    )
    return sdr, hdr


@pytest.mark.asyncio
async def test_same_name_hdr_sdr_pair_is_paired_on_import(duplicate_library):
    from backend.services.screenshot_service import get_capture_details

    db, _ = duplicate_library
    sdr, hdr = await _create_named_pair(db, hdr_name="SHOT.jxr")

    assert hdr["capture_group_id"] == sdr["id"]
    details = await get_capture_details(hdr["id"])
    assert sorted(variant["format"] for variant in details["variants"]) == ["jxr", "png"]


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"hdr_name": "other.jxr", "hdr_taken_at": "2026-01-02T03:04:11"},  # 6 s apart
    {"hdr_name": "shot.jxr", "hdr_hash": "f" * 32},  # different image
    {"hdr_name": "shot.jxr", "hdr_hash": "0" * 28 + "1fff"},  # 13 bits apart
    {"hdr_name": "shot.jxr", "hdr_width": 1280},  # different shape
    {"hdr_name": "shot.jxr", "hdr_taken_at": "2026-01-02T04:04:05"},  # an hour apart
    {"hdr_name": "shot.jxr", "sdr_taken_at": None, "hdr_taken_at": None,
     "hdr_hash": "f" * 32},  # no capture times, different image
])
async def test_pairing_requires_every_signal(duplicate_library, overrides):
    db, _ = duplicate_library
    sdr, hdr = await _create_named_pair(db, **overrides)

    assert hdr["capture_group_id"] == hdr["id"]
    assert sdr["capture_group_id"] == sdr["id"]


@pytest.mark.asyncio
async def test_like_wildcards_in_names_do_not_pair(duplicate_library):
    db, _ = duplicate_library
    _, hdr = await _create_named_pair(
        db, sdr_name="shotA1.png", hdr_name="shot_1.jxr",
        sdr_taken_at=None, hdr_taken_at=None,
    )

    assert hdr["capture_group_id"] == hdr["id"]


@pytest.mark.asyncio
async def test_same_name_without_capture_times_pairs(duplicate_library):
    db, _ = duplicate_library
    sdr, hdr = await _create_named_pair(
        db, hdr_name="shot.jxr", sdr_taken_at=None, hdr_taken_at=None,
    )

    assert hdr["capture_group_id"] == sdr["id"]


@pytest.mark.asyncio
async def test_steam_jpg_and_special_k_jxr_pair_across_a_minute(duplicate_library):
    """The real case: names differ by a minute, times by 1 s, images by 8 bits."""
    db, _ = duplicate_library
    sdr, hdr = await _create_named_pair(
        db,
        sdr_name="CONTROL 2026_10_02 13_39.jpg",
        hdr_name="CONTROL 2026_10_02 13_40.jxr",
        sdr_taken_at="2026-10-02T13:39:59",
        hdr_taken_at="2026-10-02T13:40:00",
        hdr_hash="0" * 30 + "ff",
    )

    assert hdr["capture_group_id"] == sdr["id"]


async def _add(db, game_id: int, name: str, image_format: str, taken_at: str,
               perceptual_hash: str = "0" * 32) -> dict:
    from backend.services.screenshot_service import create_screenshot

    return await create_screenshot(
        game_id=game_id, filename=name, file_path=f"Pairs/{name}",
        format=image_format, is_hdr=image_format == "jxr", width=3840, height=2160,
        taken_at=taken_at, perceptual_hash=perceptual_hash, recalculate_stats=False,
    )


async def _new_game(db, name: str = "Pairs") -> int:
    cursor = await db.execute(
        "INSERT INTO games (name, folder_name) VALUES (?, ?)", (name, name)
    )
    await db.commit()
    return cursor.lastrowid


@pytest.mark.asyncio
async def test_one_file_of_each_type_per_capture(duplicate_library):
    """A burst: each capture keeps its own JPG and JXR, matched by nearest time."""
    db, _ = duplicate_library
    game_id = await _new_game(db)
    jpg_a = await _add(db, game_id, "a.jpg", "jpeg", "2026-10-02T13:59:41")
    jpg_b = await _add(db, game_id, "b.jpg", "jpeg", "2026-10-02T13:59:44")
    jxr_b = await _add(db, game_id, "b.jxr", "jxr", "2026-10-02T13:59:45")
    jxr_a = await _add(db, game_id, "a.jxr", "jxr", "2026-10-02T13:59:42")
    png_a = await _add(db, game_id, "a.png", "png", "2026-10-02T13:59:42")

    assert jxr_b["capture_group_id"] == jpg_b["id"]
    assert jxr_a["capture_group_id"] == jpg_a["id"]
    assert png_a["capture_group_id"] == jpg_a["id"]


@pytest.mark.asyncio
async def test_two_jpgs_never_share_a_capture(duplicate_library):
    db, _ = duplicate_library
    game_id = await _new_game(db)
    first = await _add(db, game_id, "first.jpg", "jpeg", "2026-10-02T13:59:41")
    second = await _add(db, game_id, "second.jpg", "jpg", "2026-10-02T13:59:41")

    assert second["capture_group_id"] == second["id"] != first["id"]


@pytest.mark.asyncio
async def test_scan_keeps_ungrouped_variants_apart(duplicate_library):
    from backend.services import duplicate_service
    from backend.services.screenshot_service import ungroup_screenshot

    db, _ = duplicate_library
    game_id = await _new_game(db)
    jpg = await _add(db, game_id, "a.jpg", "jpeg", "2026-10-02T13:59:41")
    jxr = await _add(db, game_id, "a.jxr", "jxr", "2026-10-02T13:59:42")
    assert jxr["capture_group_id"] == jpg["id"]

    await ungroup_screenshot(jxr["id"])
    job = await duplicate_service.start_scan_job()
    await duplicate_service.run_scan_job(job["id"])

    cursor = await db.execute(
        "SELECT id, capture_group_id FROM screenshots WHERE game_id = ? ORDER BY id",
        (game_id,),
    )
    assert await cursor.fetchall() == [(jpg["id"], jpg["id"]), (jxr["id"], jxr["id"])]


@pytest.mark.asyncio
async def test_scan_pairs_existing_same_name_captures(duplicate_library):
    from backend.services import duplicate_service
    from backend.services.game_service import get_game, update_screenshot_stats
    from backend.services.screenshot_service import create_screenshot

    db, library_dir = duplicate_library
    cursor = await db.execute(
        "INSERT INTO games (name, folder_name) VALUES ('Legacy', 'Legacy')"
    )
    game_id = cursor.lastrowid
    await db.commit()
    folder = library_dir / "Legacy"
    folder.mkdir(parents=True)
    image = Image.new("RGB", (640, 360), (30, 90, 160))
    ids = []
    for name, image_format in (("shot.png", "png"), ("shot.jxr", "jxr")):
        image.save(folder / name, format="PNG")
        # No fingerprint yet, like captures imported before auto-pairing.
        created = await create_screenshot(
            game_id=game_id, filename=name, file_path=f"Legacy/{name}",
            format=image_format, width=640, height=360,
            taken_at="2026-01-02T03:04:05", recalculate_stats=False,
        )
        ids.append(created["id"])
    await update_screenshot_stats(game_id)
    assert (await get_game(game_id))["screenshot_count"] == 2

    job = await duplicate_service.start_scan_job()
    await duplicate_service.run_scan_job(job["id"])

    cursor = await db.execute(
        "SELECT capture_group_id FROM screenshots WHERE game_id = ? ORDER BY id", (game_id,)
    )
    assert [row[0] for row in await cursor.fetchall()] == [ids[0], ids[0]]
    assert (await get_game(game_id))["screenshot_count"] == 1
    pending, _ = await duplicate_service.list_candidates()
    assert pending == []
    grouped, _ = await duplicate_service.list_candidates("grouped")
    assert [item["reason"] for item in grouped] == ["same_capture"]


@pytest.mark.asyncio
async def test_upgrade_regroups_existing_library_in_the_background(duplicate_library):
    """m017 queues a scan; startup runs it, so 1.1.0 libraries regroup on their own."""
    import asyncio

    from backend.migrations.m017_repair_capture_variants import upgrade
    from backend.services import duplicate_service

    db, _ = duplicate_library
    game_id = await _new_game(db, "Legacy")
    ids = []
    for name, image_format, taken_at in (
        ("a 13_39.jpg", "jpeg", "2026-10-02T13:39:59"),
        ("a 13_40.jxr", "jxr", "2026-10-02T13:40:00"),
    ):
        # Imported by 1.1.0: fingerprinted, but never grouped.
        cursor = await db.execute(
            """INSERT INTO screenshots (game_id, filename, file_path, format, width,
                   height, taken_at, perceptual_hash, capture_group_id)
               VALUES (?, ?, ?, ?, 3840, 2160, ?, ?, NULL)""",
            (game_id, name, f"Legacy/{name}", image_format, taken_at, "0" * 30 + "ff"),
        )
        ids.append(cursor.lastrowid)
    await db.commit()

    await upgrade(db)
    await db.commit()
    await duplicate_service.resume_scan_job()
    await asyncio.wait_for(duplicate_service._startup_scan, timeout=10)

    cursor = await db.execute(
        "SELECT capture_group_id FROM screenshots WHERE game_id = ? ORDER BY id", (game_id,)
    )
    assert [row[0] for row in await cursor.fetchall()] == [ids[0], ids[0]]
    assert (await duplicate_service.get_latest_scan_job())["status"] == "completed"
