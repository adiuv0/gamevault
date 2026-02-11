"""Tests for core GameVault services: database, auth, filesystem, image processing."""

from pathlib import Path

import pytest

# Environment overrides are set in conftest.py (runs before this module).
# The settings singleton is created once at import time of backend.config.


# ── Database Tests ───────────────────────────────────────────────────────────


class TestDatabase:
    @pytest.fixture(autouse=True)
    async def setup_db(self):
        from backend.database import init_db, close_db
        await init_db()
        yield
        await close_db()

    @pytest.mark.asyncio
    async def test_db_initializes(self):
        from backend.database import get_db
        db = await get_db()
        assert db is not None

    @pytest.mark.asyncio
    async def test_tables_exist(self):
        from backend.database import get_db
        db = await get_db()
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        rows = await cursor.fetchall()
        table_names = [r[0] for r in rows]

        expected = [
            "games", "screenshots", "annotations", "share_links",
            "steam_import_sessions", "tags", "screenshot_tags", "app_settings",
        ]
        for table in expected:
            assert table in table_names, f"Missing table: {table}"

    @pytest.mark.asyncio
    async def test_fts5_table_exists(self):
        from backend.database import get_db
        db = await get_db()
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%fts%'"
        )
        rows = await cursor.fetchall()
        fts_tables = [r[0] for r in rows]
        assert any("screenshots_fts" in t for t in fts_tables), f"FTS5 table missing. Found: {fts_tables}"

    @pytest.mark.asyncio
    async def test_wal_mode(self):
        from backend.database import get_db
        db = await get_db()
        cursor = await db.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        assert row[0] == "wal"


# ── Auth Tests ───────────────────────────────────────────────────────────────


class TestAuth:
    def test_hash_password(self):
        from backend.auth import hash_password, verify_password
        hashed = hash_password("mypassword")
        assert hashed != "mypassword"
        assert verify_password("mypassword", hashed)
        assert not verify_password("wrongpassword", hashed)

    def test_create_and_decode_token(self):
        from backend.auth import create_access_token, decode_token
        token = create_access_token(expires_days=1)
        payload = decode_token(token)
        assert payload["sub"] == "gamevault_user"
        assert "exp" in payload

    def test_valid_token_decodes(self):
        from backend.auth import create_access_token, decode_token
        token = create_access_token(expires_days=1)
        payload = decode_token(token)
        assert payload is not None

    def test_invalid_token_raises(self):
        from backend.auth import decode_token
        from jose import JWTError
        with pytest.raises(JWTError):
            decode_token("not.a.valid.token")

    def test_different_secrets_fail(self):
        """Token from one secret should fail with another."""
        from backend.auth import create_access_token
        from jose import jwt, JWTError
        token = create_access_token(expires_days=1)
        with pytest.raises(JWTError):
            jwt.decode(token, "wrong-secret", algorithms=["HS256"])


# ── Filesystem Tests ─────────────────────────────────────────────────────────


class TestFilesystem:
    def test_sanitize_filename_basic(self):
        from backend.services.filesystem import sanitize_filename
        assert sanitize_filename("hello world") == "hello world"

    def test_sanitize_filename_special_chars(self):
        from backend.services.filesystem import sanitize_filename
        result = sanitize_filename('file<>:"/\\|?*name.png')
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result
        assert '"' not in result
        assert result.endswith(".png")

    def test_sanitize_filename_windows_reserved(self):
        from backend.services.filesystem import sanitize_filename
        result = sanitize_filename("CON")
        assert result != "CON"
        assert result == "_CON"

    def test_sanitize_filename_empty(self):
        from backend.services.filesystem import sanitize_filename
        result = sanitize_filename("")
        # Empty input returns a safe non-empty fallback
        assert len(result) > 0

    def test_sanitize_filename_trailing_dots(self):
        from backend.services.filesystem import sanitize_filename
        result = sanitize_filename("myfile...")
        assert not result.endswith(".")

    def test_format_file_size(self):
        from backend.services.filesystem import format_file_size
        assert format_file_size(0) == "0 B"
        assert "KB" in format_file_size(1024)
        assert "MB" in format_file_size(1024 * 1024)
        assert "GB" in format_file_size(1024 * 1024 * 1024)

    def test_get_game_dir(self):
        from backend.services.filesystem import get_game_dir
        path = get_game_dir("Elden Ring")
        assert "Elden Ring" in str(path)
        assert path.is_absolute()


# ── Config Tests ─────────────────────────────────────────────────────────────


class TestConfig:
    def test_settings_loaded(self):
        from backend.config import settings
        assert settings.secret_key == "pytest-secret-key"

    def test_max_upload_size_bytes(self):
        from backend.config import settings
        assert settings.max_upload_size_bytes == settings.max_upload_size_mb * 1024 * 1024

    def test_data_paths(self):
        from backend.config import settings
        assert isinstance(settings.data_dir, Path)
        assert isinstance(settings.library_dir, Path)
        assert isinstance(settings.db_path, Path)


# ── Image Processor Tests ────────────────────────────────────────────────────


class TestImageProcessor:
    def test_generate_thumbnail_jpeg(self, tmp_path):
        """Test thumbnail creation from a JPEG image."""
        from PIL import Image as PILImage
        from backend.services.image_processor import generate_thumbnail

        img = PILImage.new("RGB", (1920, 1080), color=(100, 150, 200))
        src_path = tmp_path / "test.jpg"
        img.save(str(src_path), "JPEG")

        thumb_path = tmp_path / "thumb.jpg"
        result = generate_thumbnail(src_path, thumb_path, max_width=300)

        assert result is True
        assert thumb_path.exists()

        # Verify thumbnail is smaller
        thumb = PILImage.open(str(thumb_path))
        assert thumb.size[0] <= 300

    def test_generate_thumbnail_png_rgba(self, tmp_path):
        """Test thumbnail creation from an RGBA PNG image."""
        from PIL import Image as PILImage
        from backend.services.image_processor import generate_thumbnail

        img = PILImage.new("RGBA", (800, 600), color=(255, 0, 0, 128))
        src_path = tmp_path / "test.png"
        img.save(str(src_path), "PNG")

        thumb_path = tmp_path / "thumb.jpg"
        result = generate_thumbnail(src_path, thumb_path, max_width=200)

        assert result is True
        assert thumb_path.exists()

    def test_get_image_dimensions(self, tmp_path):
        """Test image dimension extraction."""
        from PIL import Image as PILImage
        from backend.services.image_processor import get_image_dimensions

        img = PILImage.new("RGB", (1920, 1080), color=(0, 0, 0))
        src_path = tmp_path / "test.jpg"
        img.save(str(src_path), "JPEG")

        dims = get_image_dimensions(src_path)
        assert dims is not None
        assert dims == (1920, 1080)

    def test_get_image_format(self, tmp_path):
        """Test image format detection."""
        from PIL import Image as PILImage
        from backend.services.image_processor import get_image_format

        img = PILImage.new("RGB", (100, 100), color=(0, 0, 0))
        src_path = tmp_path / "test.png"
        img.save(str(src_path), "PNG")

        fmt = get_image_format(src_path)
        assert fmt == "png"

    def test_compute_sha256(self, tmp_path):
        """Test SHA256 hash computation."""
        from backend.services.image_processor import compute_sha256

        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"hello world")

        hash1 = compute_sha256(test_file)
        hash2 = compute_sha256(test_file)
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex digest

    def test_different_files_different_hashes(self, tmp_path):
        """Different content should produce different hashes."""
        from backend.services.image_processor import compute_sha256

        file1 = tmp_path / "a.bin"
        file2 = tmp_path / "b.bin"
        file1.write_bytes(b"content a")
        file2.write_bytes(b"content b")

        assert compute_sha256(file1) != compute_sha256(file2)

    def test_compute_sha256_bytes(self):
        """Test SHA256 from raw bytes."""
        from backend.services.image_processor import compute_sha256_bytes

        h1 = compute_sha256_bytes(b"hello")
        h2 = compute_sha256_bytes(b"hello")
        h3 = compute_sha256_bytes(b"world")

        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 64

    def test_validate_image(self, tmp_path):
        """Test image validation."""
        from PIL import Image as PILImage
        from backend.services.image_processor import validate_image

        # Valid image
        img = PILImage.new("RGB", (100, 100), color=(0, 0, 0))
        src_path = tmp_path / "valid.jpg"
        img.save(str(src_path), "JPEG")
        assert validate_image(src_path) is True

        # Invalid file
        bad_path = tmp_path / "bad.jpg"
        bad_path.write_bytes(b"not an image at all")
        assert validate_image(bad_path) is False

    def test_thumbnail_preserves_aspect_ratio(self, tmp_path):
        """Thumbnails should preserve original aspect ratio."""
        from PIL import Image as PILImage
        from backend.services.image_processor import generate_thumbnail

        # 16:9 image
        img = PILImage.new("RGB", (1600, 900), color=(0, 0, 0))
        src_path = tmp_path / "wide.jpg"
        img.save(str(src_path), "JPEG")

        thumb_path = tmp_path / "thumb.jpg"
        generate_thumbnail(src_path, thumb_path, max_width=400)

        thumb = PILImage.open(str(thumb_path))
        w, h = thumb.size
        ratio = w / h
        assert abs(ratio - (16 / 9)) < 0.02  # Within 2% of 16:9


# ── Game Service Tests ───────────────────────────────────────────────────────


class TestGameService:
    @pytest.fixture(autouse=True)
    async def setup_db(self):
        from backend.database import init_db, close_db
        await init_db()
        yield
        await close_db()

    @pytest.mark.asyncio
    async def test_create_game(self):
        from backend.services.game_service import create_game
        game = await create_game("Test Game")
        assert game["name"] == "Test Game"
        assert game["id"] is not None

    @pytest.mark.asyncio
    async def test_get_game(self):
        from backend.services.game_service import create_game, get_game
        created = await create_game("Retrieval Test")
        fetched = await get_game(created["id"])
        assert fetched is not None
        assert fetched["name"] == "Retrieval Test"

    @pytest.mark.asyncio
    async def test_get_nonexistent_game(self):
        from backend.services.game_service import get_game
        result = await get_game(99999)
        assert result is None

    @pytest.mark.asyncio
    async def test_list_games(self):
        from backend.services.game_service import create_game, list_games
        await create_game("Game A")
        await create_game("Game B")
        games = await list_games()
        assert len(games) >= 2

    @pytest.mark.asyncio
    async def test_get_or_create_game(self):
        from backend.services.game_service import get_or_create_game
        game1 = await get_or_create_game("UniqueGame", steam_app_id=12345)
        game2 = await get_or_create_game("UniqueGame", steam_app_id=12345)
        assert game1["id"] == game2["id"]

    @pytest.mark.asyncio
    async def test_delete_game(self):
        from backend.services.game_service import create_game, delete_game, get_game
        game = await create_game("To Delete")
        await delete_game(game["id"])
        result = await get_game(game["id"])
        assert result is None


# ── Search Service Tests ─────────────────────────────────────────────────────


class TestSearchService:
    @pytest.fixture(autouse=True)
    async def setup_db(self, tmp_path):
        import backend.database as db_mod
        from backend.config import settings

        # Use a unique DB file for this test class to avoid "database is locked"
        # collisions with other test classes sharing the singleton.
        original_db_path = settings.db_path
        settings.db_path = tmp_path / "search_test.db"

        # Reset the singleton so init_db creates a fresh connection
        if db_mod._db is not None:
            try:
                await db_mod._db.close()
            except Exception:
                pass
            db_mod._db = None

        await db_mod.init_db()

        # Seed test data
        db = await db_mod.get_db()
        await db.execute(
            """INSERT INTO games (name, folder_name, screenshot_count)
               VALUES ('Elden Ring', 'elden-ring', 1)"""
        )
        await db.execute(
            """INSERT INTO screenshots (game_id, filename, file_path, source, uploaded_at)
               VALUES (1, 'boss_fight.png', '/tmp/boss.png', 'upload', '2024-01-15')"""
        )
        # Populate FTS manually
        await db.execute(
            """INSERT OR REPLACE INTO screenshots_fts (rowid, game_name, filename, steam_description, annotation_content)
               VALUES (1, 'Elden Ring', 'boss_fight.png', '', '')"""
        )
        await db.commit()
        yield
        await db_mod.close_db()
        settings.db_path = original_db_path

    @pytest.mark.asyncio
    async def test_search_by_game_name(self):
        from backend.services.search_service import search_screenshots
        results, total = await search_screenshots("Elden Ring")
        assert total > 0

    @pytest.mark.asyncio
    async def test_search_by_filename(self):
        from backend.services.search_service import search_screenshots
        results, total = await search_screenshots("boss_fight")
        assert total > 0

    @pytest.mark.asyncio
    async def test_search_no_results(self):
        from backend.services.search_service import search_screenshots
        results, total = await search_screenshots("nonexistent_game_xyz")
        assert total == 0

    @pytest.mark.asyncio
    async def test_search_empty_query(self):
        from backend.services.search_service import search_screenshots
        results, total = await search_screenshots("")
        assert isinstance(total, int)
