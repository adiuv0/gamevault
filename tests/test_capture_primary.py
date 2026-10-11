"""The stored capture representative must match the old window-ranked definition."""

import pytest


@pytest.fixture
async def library(monkeypatch, tmp_path):
    from backend.config import settings
    from backend.database import close_db, init_db
    await close_db()
    for field, path in {"data_dir": tmp_path, "db_path": tmp_path / "state.db",
                        "library_dir": tmp_path / "library", "thumbnail_dir": tmp_path / "cache"}.items():
        monkeypatch.setattr(settings, field, path)
    await init_db()
    yield settings
    await close_db()


async def _assert_matches_window_ranking():
    from backend.database import get_db
    db = await get_db()
    cursor = await db.execute(
        """SELECT id FROM (
               SELECT id, ROW_NUMBER() OVER (
                   PARTITION BY game_id, COALESCE(capture_group_id, id)
                   ORDER BY COALESCE(is_hdr, lower(COALESCE(format, '')) IN ('jxr', 'wdp')), id
               ) AS capture_rank FROM screenshots
           ) WHERE capture_rank = 1 ORDER BY id"""
    )
    expected = [row[0] for row in await cursor.fetchall()]
    cursor = await db.execute("SELECT id FROM screenshots WHERE is_capture_primary = 1 ORDER BY id")
    assert [row[0] for row in await cursor.fetchall()] == expected
    return expected


async def test_primary_follows_grouping_ungrouping_deletes_and_merges(library):
    from backend.database import get_db
    from backend.services import game_service as games
    from backend.services import screenshot_service as shots

    game = await games.create_game("Primary")
    other = await games.create_game("Merge target")
    hdr = await shots.create_screenshot(game["id"], "a.jxr", "a.jxr", is_hdr=True)
    sdr = await shots.create_screenshot(
        game["id"], "a.png", "a.png", is_hdr=False, capture_group_id=hdr["id"]
    )
    lone = await shots.create_screenshot(game["id"], "b.png", "b.png")
    # The SDR variant represents the capture even though the HDR file came first.
    assert await _assert_matches_window_ranking() == [sdr["id"], lone["id"]]

    await shots.ungroup_screenshot(sdr["id"])
    assert await _assert_matches_window_ranking() == [hdr["id"], sdr["id"], lone["id"]]

    db = await get_db()
    await db.execute(
        "UPDATE screenshots SET capture_group_id = ? WHERE id = ?", (hdr["id"], lone["id"])
    )
    await db.commit()
    assert await _assert_matches_window_ranking() == [sdr["id"], lone["id"]]

    await db.execute("UPDATE screenshots SET is_hdr = 0 WHERE id = ?", (hdr["id"],))
    await db.commit()
    assert await _assert_matches_window_ranking() == [hdr["id"], sdr["id"]]

    await shots.delete_screenshot(hdr["id"])
    assert await _assert_matches_window_ranking() == [sdr["id"], lone["id"]]

    await games.merge_games(game["id"], other["id"])
    assert await _assert_matches_window_ranking() == [sdr["id"], lone["id"]]


async def test_migration_backfills_existing_rows(tmp_path):
    import importlib

    import aiosqlite

    from backend.migrations.runner import MIGRATIONS

    db = await aiosqlite.connect(tmp_path / "upgrade.db")
    try:
        index = MIGRATIONS.index("backend.migrations.m015_capture_primary")
        for name in MIGRATIONS[:index]:
            await importlib.import_module(name).upgrade(db)
        await db.execute("INSERT INTO games (id, name, folder_name) VALUES (1, 'G', 'G')")
        await db.executemany(
            """INSERT INTO screenshots (id, game_id, filename, file_path, format, capture_group_id)
               VALUES (?, 1, ?, ?, ?, ?)""",
            [(1, "x.jxr", "x.jxr", "jxr", 1), (2, "x.png", "x.png", "png", 1),
             (3, "y.png", "y.png", "png", None)],
        )
        await importlib.import_module(MIGRATIONS[index]).upgrade(db)
        cursor = await db.execute("SELECT id FROM logical_captures ORDER BY id")
        assert [row[0] for row in await cursor.fetchall()] == [2, 3]
    finally:
        await db.close()
