"""Regression tests for gallery and media performance foundations."""

import aiosqlite
import pytest


@pytest.fixture
async def performance_db(monkeypatch, tmp_path):
    from backend.config import settings
    from backend.database import close_db, get_db, init_db

    await close_db()
    monkeypatch.setattr(settings, "db_path", tmp_path / "performance.db")
    await init_db()
    try:
        yield await get_db()
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_gallery_sort_uses_composite_index(performance_db):
    cursor = await performance_db.execute(
        """EXPLAIN QUERY PLAN
           SELECT id FROM screenshots
           WHERE game_id = ?
           ORDER BY taken_at DESC, id DESC
           LIMIT 50""",
        (1,),
    )
    plan = " ".join(row[3] for row in await cursor.fetchall())
    assert "idx_screenshots_game_taken_id" in plan
    assert "TEMP B-TREE" not in plan


@pytest.mark.asyncio
async def test_media_lookup_returns_only_serve_fields(performance_db):
    from backend.services.screenshot_service import get_screenshot_media

    cursor = await performance_db.execute(
        "INSERT INTO games (name, folder_name) VALUES ('Test', 'Test')"
    )
    game_id = cursor.lastrowid
    cursor = await performance_db.execute(
        """INSERT INTO screenshots
           (game_id, filename, file_path, thumbnail_path_sm, thumbnail_path_md, format)
           VALUES (?, 'shot.png', 'Test/screenshots/shot.png',
                   'Test/thumbnails/300/shot.jpg',
                   'Test/thumbnails/800/shot.jpg', 'png')""",
        (game_id,),
    )
    screenshot_id = cursor.lastrowid
    await performance_db.commit()

    media = await get_screenshot_media(screenshot_id)
    assert set(media) == {
        "id",
        "file_path",
        "thumbnail_path_sm",
        "thumbnail_path_md",
        "preview_path",
        "format",
        "media_version",
    }


@pytest.mark.asyncio
async def test_migrates_legacy_thumbnails(performance_db, monkeypatch, tmp_path):
    from backend.config import settings
    from backend.services.thumbnail_cache_service import migrate_legacy_thumbnails

    library_dir = tmp_path / "library"
    thumbnail_dir = tmp_path / "cache" / "thumbnails"
    monkeypatch.setattr(settings, "library_dir", library_dir)
    monkeypatch.setattr(settings, "thumbnail_dir", thumbnail_dir)

    cursor = await performance_db.execute(
        "INSERT INTO games (name, folder_name) VALUES ('Test', 'Test')"
    )
    game_id = cursor.lastrowid
    await performance_db.execute(
        """INSERT INTO screenshots
           (game_id, filename, file_path, thumbnail_path_sm, thumbnail_path_md)
           VALUES (?, 'shot.png', 'Test/screenshots/shot.png',
                   'Test/thumbnails/300/shot.jpg',
                   'Test/thumbnails/800/shot.jpg')""",
        (game_id,),
    )
    await performance_db.commit()

    legacy_sm = library_dir / "Test" / "thumbnails" / "300" / "shot.jpg"
    legacy_md = library_dir / "Test" / "thumbnails" / "800" / "shot.jpg"
    legacy_sm.parent.mkdir(parents=True)
    legacy_md.parent.mkdir(parents=True)
    legacy_sm.write_bytes(b"sm")
    legacy_md.write_bytes(b"md")

    result = await migrate_legacy_thumbnails()

    assert result == {"moved": 2, "already_cached": 0, "missing": 0, "invalid": 0}
    assert (thumbnail_dir / "Test" / "300" / "shot.jpg").read_bytes() == b"sm"
    assert (thumbnail_dir / "Test" / "800" / "shot.jpg").read_bytes() == b"md"
    assert not legacy_sm.exists()
    assert not legacy_md.exists()


def test_thumbnail_pair_decodes_source_once(monkeypatch, tmp_path):
    from PIL import Image

    from backend.config import settings
    from backend.services import image_processor

    source = tmp_path / "source.png"
    Image.new("RGB", (1200, 675), "navy").save(source)
    monkeypatch.setattr(settings, "thumbnail_dir", tmp_path / "cache")

    original_open = image_processor._open_for_thumbnail
    calls = 0

    def counted_open(path):
        nonlocal calls
        calls += 1
        return original_open(path)

    monkeypatch.setattr(image_processor, "_open_for_thumbnail", counted_open)
    sm, md = image_processor.generate_thumbnails(source, "Test", "shot")

    assert calls == 1
    assert sm == "Test/thumbnails/300/shot.jpg"
    assert md == "Test/thumbnails/800/shot.jpg"
    assert (settings.thumbnail_dir / "Test" / "300" / "shot.jpg").exists()
    assert (settings.thumbnail_dir / "Test" / "800" / "shot.jpg").exists()
    assert (
        settings.thumbnail_dir
        / "Test"
        / str(settings.preview_max_width)
        / "shot.jpg"
    ).exists()


@pytest.mark.asyncio
async def test_capture_variants_share_one_game_count(performance_db):
    from backend.services.game_service import get_game, update_screenshot_stats
    from backend.services.screenshot_service import create_screenshot

    cursor = await performance_db.execute(
        "INSERT INTO games (name, folder_name) VALUES ('HDR Test', 'HDR Test')"
    )
    game_id = cursor.lastrowid
    await performance_db.commit()

    first = await create_screenshot(
        game_id=game_id,
        filename="capture.png",
        file_path="HDR Test/screenshots/capture.png",
        source_key="specialk:capture",
        recalculate_stats=False,
    )
    second = await create_screenshot(
        game_id=game_id,
        filename="capture.jxr",
        file_path="HDR Test/screenshots/capture.jxr",
        source_key="specialk:capture",
        recalculate_stats=False,
    )
    await update_screenshot_stats(game_id)

    cursor = await performance_db.execute(
        "SELECT capture_group_id FROM screenshots WHERE id IN (?, ?) ORDER BY id",
        (first["id"], second["id"]),
    )
    capture_groups = [row[0] for row in await cursor.fetchall()]
    assert capture_groups == [first["id"], first["id"]]
    assert (await get_game(game_id))["screenshot_count"] == 1


@pytest.mark.asyncio
async def test_capture_migration_groups_existing_hdr_siblings(tmp_path):
    from backend.migrations.m007_capture_groups import upgrade

    db = await aiosqlite.connect(tmp_path / "capture-migration.db")
    try:
        await db.execute(
            """CREATE TABLE games (
                   id INTEGER PRIMARY KEY,
                   screenshot_count INTEGER NOT NULL DEFAULT 0
               )"""
        )
        await db.execute(
            """CREATE TABLE screenshots (
                   id INTEGER PRIMARY KEY,
                   game_id INTEGER NOT NULL,
                   filename TEXT NOT NULL,
                   format TEXT
               )"""
        )
        await db.execute("INSERT INTO games (id, screenshot_count) VALUES (1, 3)")
        await db.executemany(
            "INSERT INTO screenshots (id, game_id, filename, format) VALUES (?, 1, ?, ?)",
            [
                (10, "specialk_20260101_capture.png", "png"),
                (11, "specialk_20260101_capture.jxr", "jxr"),
                (12, "different.png", "png"),
            ],
        )

        await upgrade(db)
        cursor = await db.execute(
            "SELECT id, capture_group_id FROM screenshots ORDER BY id"
        )
        assert await cursor.fetchall() == [(10, 10), (11, 10), (12, 12)]
        cursor = await db.execute("SELECT screenshot_count FROM games WHERE id = 1")
        assert (await cursor.fetchone())[0] == 2
    finally:
        await db.close()
