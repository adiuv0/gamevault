"""Tests for the game-merge feature.

Covers:
  - get_game_by_name is case-insensitive (prevents Special K importer
    creating ``Cyberpunk 2077`` next to existing ``CYBERPUNK 2077``).
  - merge_games moves screenshot rows + files + thumbnails into the
    target, handles filename collisions, transfers cover when the
    target lacks one, re-syncs FTS, deletes the source row + folder.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
async def db_with_library(monkeypatch, tmp_path: Path):
    """Initialize the DB with library_dir pointed at a tmp tree.

    The conftest sets a single shared sqlite path for the whole test
    session, so every test wipes the games / screenshots tables on
    entry and exit to keep them isolated.
    """
    from backend.config import settings as app_settings
    from backend.database import close_db, get_db, init_db

    monkeypatch.setattr(app_settings, "library_dir", tmp_path)
    await init_db()

    db = await get_db()
    for table in (
        "annotations",
        "share_links",
        "screenshots_fts_content",
        "screenshots",
        "games",
    ):
        await db.execute(f"DELETE FROM {table}")
    await db.commit()

    yield tmp_path
    await close_db()


def _write(p: Path, data: bytes = b"x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


# ── Case-insensitive name matching ───────────────────────────────────────────


class TestCaseInsensitiveNameMatch:
    @pytest.mark.asyncio
    async def test_get_game_by_name_is_ci(self, db_with_library):
        from backend.services import game_service

        await game_service.create_game(name="DOOM Eternal")

        # Various casings all return the same row
        for q in ("DOOM Eternal", "doom eternal", "Doom Eternal", "DOOM ETERNAL"):
            game = await game_service.get_game_by_name(q)
            assert game is not None, q
            assert game["name"] == "DOOM Eternal"

    @pytest.mark.asyncio
    async def test_get_or_create_finds_existing_with_different_case(self, db_with_library):
        """Special K's cleaned folder name 'Doom Eternal' should match
        an existing 'DOOM Eternal' row created by the Steam Store API."""
        from backend.services import game_service

        original = await game_service.create_game(name="DOOM Eternal")

        same = await game_service.get_or_create_game(name="Doom Eternal")
        assert same["id"] == original["id"]
        assert same["name"] == "DOOM Eternal"  # original preserved


# ── merge_games behavior ─────────────────────────────────────────────────────


class TestMergeGames:
    @pytest.mark.asyncio
    async def test_rejects_self_merge(self, db_with_library):
        from backend.services import game_service

        a = await game_service.create_game(name="A")
        with pytest.raises(ValueError, match="itself"):
            await game_service.merge_games(a["id"], a["id"])

    @pytest.mark.asyncio
    async def test_rejects_missing_source(self, db_with_library):
        from backend.services import game_service

        target = await game_service.create_game(name="Target")
        with pytest.raises(ValueError, match="Source"):
            await game_service.merge_games(99999, target["id"])

    @pytest.mark.asyncio
    async def test_rejects_missing_target(self, db_with_library):
        from backend.services import game_service

        source = await game_service.create_game(name="Source")
        with pytest.raises(ValueError, match="Target"):
            await game_service.merge_games(source["id"], 99999)

    @pytest.mark.asyncio
    async def test_moves_screenshots_to_target(
        self, db_with_library, tmp_path: Path
    ):
        from backend.services import game_service, screenshot_service

        source = await game_service.create_game(name="Cyberpunk 2077")
        target = await game_service.create_game(name="Cyberpunk")

        # Plant two on-disk files for the source's screenshots
        s_folder = source["folder_name"]
        _write(tmp_path / s_folder / "screenshots" / "shot_a.jpg", b"AAAA")
        _write(tmp_path / s_folder / "thumbnails" / "300" / "shot_a.jpg", b"sm")
        _write(tmp_path / s_folder / "thumbnails" / "800" / "shot_a.jpg", b"md")

        s1 = await screenshot_service.create_screenshot(
            game_id=source["id"],
            filename="shot_a.jpg",
            file_path=f"{s_folder}/screenshots/shot_a.jpg",
            thumbnail_path_sm=f"{s_folder}/thumbnails/300/shot_a.jpg",
            thumbnail_path_md=f"{s_folder}/thumbnails/800/shot_a.jpg",
            sha256_hash="hash_a",
        )

        result = await game_service.merge_games(source["id"], target["id"])

        assert result["moved"] == 1
        assert result["had_collisions"] == 0
        assert result["target_id"] == target["id"]

        # Source DB row gone
        assert (await game_service.get_game(source["id"])) is None

        # Screenshot row now points at target
        moved = await screenshot_service.get_screenshot(s1["id"])
        assert moved["game_id"] == target["id"]

        # Files actually live in the target folder now
        t_folder = target["folder_name"]
        assert (tmp_path / t_folder / "screenshots" / "shot_a.jpg").read_bytes() == b"AAAA"
        assert (tmp_path / t_folder / "thumbnails" / "300" / "shot_a.jpg").exists()
        assert (tmp_path / t_folder / "thumbnails" / "800" / "shot_a.jpg").exists()
        assert moved["file_path"] == f"{t_folder}/screenshots/shot_a.jpg"

        # Source folder is cleaned up
        assert not (tmp_path / s_folder).exists()

    @pytest.mark.asyncio
    async def test_resolves_filename_collisions(
        self, db_with_library, tmp_path: Path
    ):
        from backend.services import game_service, screenshot_service

        source = await game_service.create_game(name="Source")
        target = await game_service.create_game(name="Target")
        s_folder = source["folder_name"]
        t_folder = target["folder_name"]

        # Both games have a "shot.jpg"
        _write(tmp_path / s_folder / "screenshots" / "shot.jpg", b"S")
        _write(tmp_path / t_folder / "screenshots" / "shot.jpg", b"T")

        await screenshot_service.create_screenshot(
            game_id=source["id"],
            filename="shot.jpg",
            file_path=f"{s_folder}/screenshots/shot.jpg",
            sha256_hash="hsrc",
        )
        await screenshot_service.create_screenshot(
            game_id=target["id"],
            filename="shot.jpg",
            file_path=f"{t_folder}/screenshots/shot.jpg",
            sha256_hash="htgt",
        )

        result = await game_service.merge_games(source["id"], target["id"])

        assert result["moved"] == 1
        assert result["had_collisions"] == 1

        # Target now has both: original + the renamed merged-in copy
        assert (tmp_path / t_folder / "screenshots" / "shot.jpg").read_bytes() == b"T"
        assert (tmp_path / t_folder / "screenshots" / "shot (1).jpg").read_bytes() == b"S"

    @pytest.mark.asyncio
    async def test_transfers_cover_when_target_lacks_one(
        self, db_with_library, tmp_path: Path
    ):
        from backend.services import game_service

        source = await game_service.create_game(name="Has Cover")
        target = await game_service.create_game(name="No Cover")
        s_folder = source["folder_name"]

        # Plant a cover for source
        cover = tmp_path / s_folder / "cover.jpg"
        _write(cover, b"COVER_BYTES")
        await game_service.update_game(
            source["id"], cover_image_path=f"{s_folder}/cover.jpg"
        )

        await game_service.merge_games(source["id"], target["id"])

        target_after = await game_service.get_game(target["id"])
        assert target_after["cover_image_path"] is not None
        new_cover = tmp_path / target_after["cover_image_path"]
        assert new_cover.exists()
        assert new_cover.read_bytes() == b"COVER_BYTES"

    @pytest.mark.asyncio
    async def test_keeps_target_cover_if_present(
        self, db_with_library, tmp_path: Path
    ):
        """Source cover is dropped (along with the source folder) if the
        target already has its own cover."""
        from backend.services import game_service

        source = await game_service.create_game(name="Source")
        target = await game_service.create_game(name="Target")
        s_folder = source["folder_name"]
        t_folder = target["folder_name"]

        _write(tmp_path / s_folder / "cover.jpg", b"SOURCE_COVER")
        _write(tmp_path / t_folder / "cover.jpg", b"TARGET_COVER")
        await game_service.update_game(
            source["id"], cover_image_path=f"{s_folder}/cover.jpg"
        )
        await game_service.update_game(
            target["id"], cover_image_path=f"{t_folder}/cover.jpg"
        )

        await game_service.merge_games(source["id"], target["id"])

        target_after = await game_service.get_game(target["id"])
        # Target's original cover preserved
        assert target_after["cover_image_path"] == f"{t_folder}/cover.jpg"
        assert (tmp_path / t_folder / "cover.jpg").read_bytes() == b"TARGET_COVER"

    @pytest.mark.asyncio
    async def test_updates_target_screenshot_count(
        self, db_with_library, tmp_path: Path
    ):
        from backend.services import game_service, screenshot_service

        source = await game_service.create_game(name="Src")
        target = await game_service.create_game(name="Tgt")
        s_folder = source["folder_name"]
        t_folder = target["folder_name"]

        _write(tmp_path / s_folder / "screenshots" / "a.jpg")
        _write(tmp_path / t_folder / "screenshots" / "b.jpg")

        await screenshot_service.create_screenshot(
            game_id=source["id"], filename="a.jpg",
            file_path=f"{s_folder}/screenshots/a.jpg", sha256_hash="ha"
        )
        await screenshot_service.create_screenshot(
            game_id=target["id"], filename="b.jpg",
            file_path=f"{t_folder}/screenshots/b.jpg", sha256_hash="hb"
        )

        await game_service.merge_games(source["id"], target["id"])

        target_after = await game_service.get_game(target["id"])
        assert target_after["screenshot_count"] == 2

    @pytest.mark.asyncio
    async def test_handles_missing_source_files_gracefully(
        self, db_with_library, tmp_path: Path
    ):
        """If a source screenshot's file is missing on disk (orphaned DB
        row), the merge should still update the row rather than crash."""
        from backend.services import game_service, screenshot_service

        source = await game_service.create_game(name="Src")
        target = await game_service.create_game(name="Tgt")

        # DB row points at a file that doesn't exist
        await screenshot_service.create_screenshot(
            game_id=source["id"], filename="ghost.jpg",
            file_path=f"{source['folder_name']}/screenshots/ghost.jpg",
            sha256_hash="hghost",
        )

        result = await game_service.merge_games(source["id"], target["id"])
        assert result["moved"] == 1


# ── HTTP endpoint smoke ──────────────────────────────────────────────────────


class TestMergeEndpoint:
    @pytest.mark.asyncio
    async def test_400_on_self_merge(self, db_with_library):
        from backend.routers.games import GameMergeRequest, merge_into
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await merge_into(source_id=42, body=GameMergeRequest(target_id=42))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_404_on_missing_game(self, db_with_library):
        from backend.routers.games import GameMergeRequest, merge_into
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await merge_into(source_id=99998, body=GameMergeRequest(target_id=99999))
        assert exc.value.status_code == 404
