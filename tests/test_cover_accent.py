"""Accent colours taken from game covers (migration m019)."""

import colorsys

import pytest
from PIL import Image

from backend.config import settings
from backend.database import close_db, get_db, init_db
from backend.services import game_service
from backend.services.cover_accent import accent_from_image, backfill_missing_accents


def _hue(color: str) -> float:
    red, green, blue = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(red, green, blue)[0]


def _cover(path, color, size=(60, 90)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def test_accent_follows_the_dominant_vivid_hue(tmp_path):
    image = Image.new("RGB", (60, 90), (12, 12, 14))
    image.paste((230, 90, 20), (0, 0, 60, 30))
    path = tmp_path / "cover.png"
    image.save(path)

    accent = accent_from_image(path)

    assert len(accent) == 7 and accent.startswith("#")
    assert abs(_hue(accent) - _hue("#e65a14")) < 0.04
    # Bright enough to read on the dark background.
    assert colorsys.rgb_to_hls(*(int(accent[i:i + 2], 16) / 255 for i in (1, 3, 5)))[1] >= 0.55


def test_reds_either_side_of_zero_stay_red(tmp_path):
    image = Image.new("RGB", (60, 90), (230, 20, 40))
    image.paste((230, 40, 20), (0, 0, 60, 45))
    path = tmp_path / "cover.png"
    image.save(path)

    hue = _hue(accent_from_image(path))
    assert hue < 0.05 or hue > 0.95


def test_greyscale_and_unreadable_covers_have_no_accent(tmp_path):
    assert accent_from_image(_cover(tmp_path / "grey.png", (120, 120, 120))) == ""
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")
    assert accent_from_image(broken) == ""
    assert accent_from_image(tmp_path / "missing.jpg") == ""


@pytest.mark.asyncio
async def test_cover_save_backfill_and_merge_keep_accents(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "library_dir", tmp_path / "library")
    monkeypatch.setattr(settings, "db_path", tmp_path / "accent.db")
    await close_db()
    await init_db()
    try:
        saved = await game_service.create_game("Accent Saved")
        _cover(tmp_path / "blue.png", (30, 60, 220))
        await game_service.save_cover_image(
            saved["id"], (tmp_path / "blue.png").read_bytes(), "cover.png"
        )
        saved = await game_service.get_game(saved["id"])
        assert abs(_hue(saved["accent_color"]) - _hue("#1e3cdc")) < 0.04

        # A cover that predates the column: filled in by the startup backfill.
        old = await game_service.create_game("Accent Old")
        grey = await game_service.create_game("Accent Grey")
        _cover(settings.library_dir / old["folder_name"] / "cover.png", (20, 200, 90))
        _cover(settings.library_dir / grey["folder_name"] / "cover.png", (90, 90, 90))
        db = await get_db()
        for game in (old, grey):
            await db.execute(
                "UPDATE games SET cover_image_path = ? WHERE id = ?",
                (f"{game['folder_name']}/cover.png", game["id"]),
            )
        await db.commit()
        before = (await game_service.get_game(old["id"]))["updated_at"]

        assert await backfill_missing_accents() == 2
        assert await backfill_missing_accents() == 0

        old = await game_service.get_game(old["id"])
        assert abs(_hue(old["accent_color"]) - _hue("#14c85a")) < 0.04
        assert old["updated_at"] == before
        assert (await game_service.get_game(grey["id"]))["accent_color"] == ""

        # Merging into a game without a cover carries the accent with the cover.
        target = await game_service.create_game("Accent Target")
        await game_service.merge_games(old["id"], target["id"])
        target = await game_service.get_game(target["id"])
        assert target["cover_image_path"] and target["accent_color"] == old["accent_color"]
    finally:
        await close_db()
