"""Regression tests for logical-capture cursor pagination."""

from pathlib import Path

import pytest


@pytest.fixture
async def cursor_db(monkeypatch, tmp_path: Path):
    from backend.config import settings
    from backend.database import close_db, get_db, init_db

    await close_db()
    monkeypatch.setattr(settings, "db_path", tmp_path / "cursor.db")
    await init_db()
    db = await get_db()
    cursor = await db.execute(
        "INSERT INTO games (name, folder_name) VALUES ('Cursor', 'Cursor')"
    )
    game_id = cursor.lastrowid
    await db.executemany(
        """INSERT INTO screenshots
           (id, game_id, filename, file_path, format, taken_at, capture_group_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (1, game_id, "echo.png", "Cursor/echo.png", "png", None, 1),
            (2, game_id, "alpha.png", "Cursor/alpha.png", "png", "2024-01-01", 2),
            (3, game_id, "charlie.png", "Cursor/charlie.png", "png", "2024-03-01", 3),
            (4, game_id, "charlie.jxr", "Cursor/charlie.jxr", "jxr", "2024-03-01", 3),
            (5, game_id, "bravo.png", "Cursor/bravo.png", "png", "2024-02-01", 5),
            (6, game_id, "foxtrot.png", "Cursor/foxtrot.png", "png", None, 6),
            (7, game_id, "delta.png", "Cursor/delta.png", "png", "2024-03-01", 7),
        ],
    )
    await db.commit()
    try:
        yield game_id
    finally:
        await close_db()


@pytest.mark.asyncio
@pytest.mark.parametrize("sort", ["date_desc", "date_asc", "name"])
async def test_cursor_pages_match_offset_order_without_duplicates(cursor_db, sort):
    from backend.services.screenshot_service import (
        list_screenshots,
        list_screenshots_cursor,
    )

    expected, expected_total = await list_screenshots(cursor_db, 1, 100, sort)
    actual = []
    cursor = None
    while True:
        page, total, cursor = await list_screenshots_cursor(
            cursor_db, cursor, limit=2, sort=sort
        )
        assert total == expected_total == 6
        actual.extend(page)
        if cursor is None:
            break

    assert [item["id"] for item in actual] == [item["id"] for item in expected]
    assert len({item["id"] for item in actual}) == len(actual) == expected_total
    grouped = next(item for item in actual if item["id"] == 3)
    assert grouped["variant_count"] == 2


@pytest.mark.asyncio
async def test_cursor_is_opaque_and_bound_to_sort(cursor_db):
    from backend.services.screenshot_service import list_screenshots_cursor

    _, _, cursor = await list_screenshots_cursor(cursor_db, limit=2, sort="date_desc")
    assert cursor is not None
    assert "2024" not in cursor

    with pytest.raises(ValueError, match="Invalid or incompatible"):
        await list_screenshots_cursor(cursor_db, cursor, limit=2, sort="name")
    with pytest.raises(ValueError, match="Invalid or incompatible"):
        await list_screenshots_cursor(cursor_db, "not-a-cursor", limit=2)
