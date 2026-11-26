"""Deleting a capture, one file of a capture, or a multi-select of captures."""

from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image


@pytest.fixture
async def library(monkeypatch, tmp_path: Path):
    from backend.config import settings
    from backend.database import close_db, get_db, init_db

    await close_db()
    for field, path in {
        "data_dir": tmp_path,
        "db_path": tmp_path / "delete.db",
        "library_dir": tmp_path / "library",
        "thumbnail_dir": tmp_path / "cache",
    }.items():
        monkeypatch.setattr(settings, field, path)
    await init_db()
    try:
        yield await get_db(), settings
    finally:
        await close_db()


async def _file(settings, game: dict, name: str, color: tuple, **fields) -> dict:
    """A real image in the library with its thumbnails and preview."""
    from backend.services.image_processor import generate_thumbnails_with_hash
    from backend.services.screenshot_service import create_screenshot

    folder = game["folder_name"]
    source = settings.library_dir / folder / "screenshots" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 36), color).save(source)
    sm, md, preview, _ = generate_thumbnails_with_hash(source, folder, Path(name).stem)
    return await create_screenshot(
        game_id=game["id"],
        filename=name,
        file_path=f"{folder}/screenshots/{name}",
        format=Path(name).suffix.lstrip("."),
        thumbnail_path_sm=sm,
        thumbnail_path_md=md,
        preview_path=preview,
        sha256_hash=f"{name}-sha",
        **fields,
    )


def _paths(settings, row: dict) -> list[Path]:
    from backend.services.filesystem import safe_library_path, safe_media_path

    return [safe_library_path(row["file_path"])] + [
        safe_media_path(row[field])
        for field in ("thumbnail_path_sm", "thumbnail_path_md", "preview_path")
    ]


async def _count(db, sql: str, params: tuple = ()) -> int:
    cursor = await db.execute(sql, params)
    return (await cursor.fetchone())[0]


@pytest.mark.asyncio
async def test_deleting_a_capture_removes_every_file_and_keeps_sync_from_reuploading(library):
    from backend.routers.screenshots import HashCheckRequest, check_hashes, delete_screenshot
    from backend.services import game_service

    db, settings = library
    game = await game_service.create_game("Delete Game")
    jpg = await _file(settings, game, "shot.jpg", (200, 10, 10))
    png = await _file(settings, game, "shot.png", (10, 200, 10), capture_group_id=jpg["id"])
    keep = await _file(settings, game, "other.jpg", (10, 10, 200))
    files = _paths(settings, jpg) + _paths(settings, png)
    assert all(path.exists() for path in files)
    assert (await game_service.get_game(game["id"]))["screenshot_count"] == 2
    revision = await _count(db, "SELECT value FROM library_revision")

    result = await delete_screenshot(png["id"])

    assert result["deleted_captures"] == 1 and result["deleted_files"] == 2
    assert not any(path.exists() for path in files)
    assert all(path.exists() for path in _paths(settings, keep))
    assert await _count(db, "SELECT COUNT(*) FROM screenshots WHERE game_id = ?", (game["id"],)) == 1
    assert (await game_service.get_game(game["id"]))["screenshot_count"] == 1
    # Timeline statistics are cached by revision; a delete must invalidate them.
    assert await _count(db, "SELECT value FROM library_revision") > revision
    # GameVault Sync is told the deleted files are already there.
    response = await check_hashes(HashCheckRequest(hashes=["shot.jpg-sha", "shot.png-sha", "new-sha"]))
    assert set(response["existing"]) == {"shot.jpg-sha", "shot.png-sha"}
    assert response["new"] == ["new-sha"]

    with pytest.raises(HTTPException) as missing:
        await delete_screenshot(png["id"])
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_deleting_one_variant_keeps_the_capture_its_note_share_and_favourite(library):
    from backend.routers.screenshots import delete_screenshot
    from backend.services import game_service
    from backend.services import screenshot_service as shots

    db, settings = library
    game = await game_service.create_game("Variant Game")
    jpg = await _file(settings, game, "press.jpg", (200, 200, 10))
    jxr_like = await _file(settings, game, "press.png", (10, 200, 200), capture_group_id=jpg["id"])
    await shots.save_annotation(jpg["id"], "boss fight")
    await shots.toggle_favorite(jpg["id"])
    # The note and share link sit on the JPG only, the file being deleted.
    await db.execute("DELETE FROM annotations WHERE screenshot_id = ?", (jxr_like["id"],))
    await db.execute(
        "INSERT INTO share_links (screenshot_id, token) VALUES (?, 'tok')", (jpg["id"],)
    )
    await db.execute("UPDATE screenshots SET is_favorite = 0 WHERE id = ?", (jxr_like["id"],))
    await db.commit()

    result = await delete_screenshot(jpg["id"], variant=True)

    assert result == {
        "message": "Screenshot deleted",
        "deleted_files": 1,
        "deleted_captures": 0,
        "removed_from_disk": 4,
        "remaining_id": jxr_like["id"],
    }
    assert not any(path.exists() for path in _paths(settings, jpg))
    assert all(path.exists() for path in _paths(settings, jxr_like))
    capture = await shots.get_capture_details(jxr_like["id"])
    assert capture["variant_count"] == 1
    assert capture["is_favorite"] and capture["has_annotation"]
    assert (await shots.get_annotation(jxr_like["id"]))["content"] == "boss fight"
    assert await _count(db, "SELECT screenshot_id FROM share_links WHERE token = 'tok'") == jxr_like["id"]
    assert (await game_service.get_game(game["id"]))["screenshot_count"] == 1
    # The group is renamed after the file that stays, so a new row can't inherit it.
    assert await _count(db, "SELECT capture_group_id FROM screenshots WHERE id = ?", (jxr_like["id"],)) == jxr_like["id"]

    # Deleting the last file of a capture deletes the capture.
    last = await delete_screenshot(jxr_like["id"], variant=True)
    assert last["deleted_captures"] == 1 and last["remaining_id"] is None
    assert (await game_service.get_game(game["id"]))["screenshot_count"] == 0


@pytest.mark.asyncio
async def test_bulk_delete_skips_unknown_ids_and_files_another_row_still_uses(library):
    from backend.routers.screenshots import BulkDeleteRequest, delete_screenshots
    from backend.services import game_service
    from backend.services import screenshot_service as shots

    db, settings = library
    game = await game_service.create_game("Bulk Game")
    first = await _file(settings, game, "a.jpg", (1, 2, 3))
    second = await _file(settings, game, "b.jpg", (4, 5, 6))
    third = await _file(settings, game, "c.jpg", (7, 8, 9))
    # A second row pointing at the same original (an old duplicate import).
    twin = await shots.create_screenshot(
        game_id=game["id"], filename="c copy.jpg", file_path=third["file_path"],
        thumbnail_path_sm=third["thumbnail_path_sm"], preview_path=third["preview_path"],
    )

    result = await delete_screenshots(BulkDeleteRequest(ids=[first["id"], second["id"], third["id"], 999_999]))

    assert result == {"deleted_files": 3, "deleted_captures": 3, "removed_from_disk": 9}
    assert all(path.exists() for path in _paths(settings, third)[:2])
    assert not _paths(settings, first)[0].exists()
    assert await _count(db, "SELECT COUNT(*) FROM screenshots") == 1
    assert (await shots.get_screenshot(twin["id"])) is not None
    assert (await game_service.get_game(game["id"]))["screenshot_count"] == 1


@pytest.mark.asyncio
async def test_imports_skip_deleted_files_until_uploaded_again_by_hand(library):
    from backend.routers.screenshots import delete_screenshot
    from backend.services import game_service
    from backend.services import screenshot_service as shots

    db, settings = library
    game = await game_service.create_game("Import Game")
    shot = await _file(settings, game, "gone.jpg", (9, 9, 9))
    await delete_screenshot(shot["id"])

    assert await shots.claim_unless_duplicate("gone", "gone.jpg-sha", set()) is True
    assert await shots.claim_unless_duplicate("fresh", "fresh-sha", set()) is False

    # The Upload page goes straight to create_screenshot: an upload on purpose.
    await shots.create_screenshot(game["id"], "gone.jpg", "x/gone.jpg", sha256_hash="gone.jpg-sha")
    assert not await shots.is_deleted_hash("gone.jpg-sha")
