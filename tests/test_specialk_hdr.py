"""Tests for HDR processing + Special K filesystem scanner."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image


# ── HDR processor unit tests ─────────────────────────────────────────────────


class TestHdrProcessor:
    def test_jxr_magic_detection(self, tmp_path: Path):
        from backend.services import hdr_processor

        jxr_file = tmp_path / "fake.jxr"
        jxr_file.write_bytes(b"\x49\x49\xbc" + b"\x00" * 100)
        assert hdr_processor.is_jxr(jxr_file) is True

        not_jxr = tmp_path / "fake.png"
        not_jxr.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        assert hdr_processor.is_jxr(not_jxr) is False

    def test_jxr_detection_handles_missing_file(self, tmp_path: Path):
        from backend.services import hdr_processor
        assert hdr_processor.is_jxr(tmp_path / "does-not-exist.jxr") is False

    def test_hdr_png_detection_for_16bit(self, tmp_path: Path):
        from backend.services import hdr_processor

        # Build a 16-bit grayscale PNG ("I;16") — Pillow handles this natively
        arr = np.full((10, 10), 50000, dtype=np.uint16)
        img = Image.fromarray(arr, mode="I;16")
        png_path = tmp_path / "hdr.png"
        img.save(png_path)
        assert hdr_processor.is_hdr_png(png_path) is True

    def test_hdr_png_detection_for_8bit_returns_false(self, tmp_path: Path):
        from backend.services import hdr_processor

        img = Image.new("RGB", (10, 10), color=(255, 0, 0))
        png_path = tmp_path / "sdr.png"
        img.save(png_path)
        assert hdr_processor.is_hdr_png(png_path) is False

    def test_is_hdr_source_combines_both(self, tmp_path: Path):
        from backend.services import hdr_processor

        sdr = tmp_path / "sdr.png"
        Image.new("RGB", (10, 10)).save(sdr)
        assert hdr_processor.is_hdr_source(sdr) is False

        jxr = tmp_path / "fake.jxr"
        jxr.write_bytes(b"\x49\x49\xbc" + b"\x00" * 100)
        assert hdr_processor.is_hdr_source(jxr) is True

    def test_reinhard_tone_map_in_unit_range(self):
        from backend.services.hdr_processor import tone_map

        rgb = np.array([[[0.0, 0.5, 1.0], [2.0, 4.0, 10.0]]], dtype=np.float32)
        out = tone_map(rgb, algorithm="reinhard", exposure=1.0)
        # Reinhard maps [0, ∞) → [0, 1)
        assert out.min() >= 0.0
        assert out.max() < 1.0
        # 0 stays 0
        assert out[0, 0, 0] == pytest.approx(0.0)
        # x = 1 → 0.5
        assert out[0, 0, 2] == pytest.approx(0.5)

    def test_aces_tone_map_clamps(self):
        from backend.services.hdr_processor import tone_map

        rgb = np.array([[[0.0, 1.0, 100.0]]], dtype=np.float32)
        out = tone_map(rgb, algorithm="aces", exposure=1.0)
        assert out.min() >= 0.0
        assert out.max() <= 1.0

    def test_clip_tone_map(self):
        from backend.services.hdr_processor import tone_map

        rgb = np.array([[[0.5, 1.5, 3.0]]], dtype=np.float32)
        out = tone_map(rgb, algorithm="clip", exposure=1.0)
        assert out[0, 0, 0] == pytest.approx(0.5)
        assert out[0, 0, 1] == pytest.approx(1.0)
        assert out[0, 0, 2] == pytest.approx(1.0)

    def test_exposure_scales_input(self):
        from backend.services.hdr_processor import tone_map

        rgb = np.array([[[0.5, 0.5, 0.5]]], dtype=np.float32)
        low = tone_map(rgb, algorithm="reinhard", exposure=0.5)
        high = tone_map(rgb, algorithm="reinhard", exposure=2.0)
        # Higher exposure brightens
        assert high[0, 0, 0] > low[0, 0, 0]

    def test_unknown_algorithm_falls_back_to_reinhard(self):
        from backend.services.hdr_processor import tone_map

        rgb = np.array([[[1.0, 1.0, 1.0]]], dtype=np.float32)
        out = tone_map(rgb, algorithm="bogus", exposure=1.0)  # type: ignore[arg-type]
        assert out[0, 0, 0] == pytest.approx(0.5)  # reinhard at x=1

    def test_to_8bit_srgb_round_trip(self):
        from backend.services.hdr_processor import to_8bit_srgb

        rgb = np.zeros((1, 1, 3), dtype=np.float32)
        out = to_8bit_srgb(rgb)
        assert out.dtype == np.uint8
        assert out[0, 0, 0] == 0

        rgb_white = np.ones((1, 1, 3), dtype=np.float32)
        out_white = to_8bit_srgb(rgb_white)
        assert out_white[0, 0, 0] == 255


# ── Special K scanner tests ──────────────────────────────────────────────────


class TestSpecialKScanner:
    def test_clean_game_name_strips_exe(self):
        from backend.services.specialk_import_service import _clean_game_name

        assert _clean_game_name("Cyberpunk2077.exe") == "Cyberpunk 2077"
        assert _clean_game_name("DOOMEternal.exe") == "DOOM Eternal"

    def test_clean_game_name_camelcase_split(self):
        from backend.services.specialk_import_service import _clean_game_name

        assert _clean_game_name("EldenRing") == "Elden Ring"
        # ALL CAPS preserved
        assert _clean_game_name("DOOM") == "DOOM"

    def test_clean_game_name_already_spaced(self):
        from backend.services.specialk_import_service import _clean_game_name

        assert _clean_game_name("The Witcher 3") == "The Witcher 3"

    def test_classify_file_by_extension(self, tmp_path: Path):
        from backend.services.specialk_import_service import _classify_file

        jxr = tmp_path / "shot.jxr"
        is_hdr, is_sdr = _classify_file(jxr)
        assert (is_hdr, is_sdr) == (True, False)

    def test_classify_file_by_folder_hint(self, tmp_path: Path):
        from backend.services.specialk_import_service import _classify_file

        hdr_dir = tmp_path / "Game" / "Screenshots" / "HDR"
        hdr_dir.mkdir(parents=True)
        png_in_hdr = hdr_dir / "shot.png"

        is_hdr, is_sdr = _classify_file(png_in_hdr)
        assert (is_hdr, is_sdr) == (True, False)

        sdr_dir = tmp_path / "Game" / "Screenshots" / "SDR"
        sdr_dir.mkdir(parents=True)
        png_in_sdr = sdr_dir / "shot.png"
        is_hdr2, is_sdr2 = _classify_file(png_in_sdr)
        assert (is_hdr2, is_sdr2) == (False, True)

    def test_scan_path_groups_by_top_level_folder(self, tmp_path: Path):
        from backend.services.specialk_import_service import scan_path

        # Two games, mixed structure
        game_a = tmp_path / "Cyberpunk2077"
        (game_a / "Screenshots" / "HDR").mkdir(parents=True)
        (game_a / "Screenshots" / "SDR").mkdir(parents=True)

        # Real PNGs (Pillow can write them; our scan only checks extension)
        Image.new("RGB", (4, 4)).save(game_a / "Screenshots" / "SDR" / "shot1.png")
        Image.new("RGB", (4, 4)).save(game_a / "Screenshots" / "SDR" / "shot2.png")
        # Fake JXR — magic bytes only; scan_path doesn't decode
        (game_a / "Screenshots" / "HDR" / "hdr1.jxr").write_bytes(
            b"\x49\x49\xbc" + b"\x00" * 100
        )

        game_b = tmp_path / "EldenRing"
        game_b.mkdir()
        Image.new("RGB", (4, 4)).save(game_b / "shot.png")

        # Non-game junk file at root — should be ignored
        (tmp_path / "readme.txt").write_text("ignore me")

        games = scan_path(tmp_path)
        assert len(games) == 2

        by_name = {g.folder_name: g for g in games}
        assert by_name["Cyberpunk2077"].suggested_name == "Cyberpunk 2077"
        assert len(by_name["Cyberpunk2077"].files) == 3
        assert by_name["Cyberpunk2077"].has_hdr is True
        assert by_name["Cyberpunk2077"].has_sdr is True

        assert by_name["EldenRing"].suggested_name == "Elden Ring"
        assert len(by_name["EldenRing"].files) == 1
        assert by_name["EldenRing"].has_hdr is False
        assert by_name["EldenRing"].has_sdr is True

    def test_scan_path_skips_folders_with_no_screenshots(self, tmp_path: Path):
        from backend.services.specialk_import_service import scan_path

        empty = tmp_path / "EmptyGame"
        empty.mkdir()
        (empty / "config.ini").write_text("nothing here")

        with_shots = tmp_path / "RealGame"
        with_shots.mkdir()
        Image.new("RGB", (4, 4)).save(with_shots / "shot.png")

        games = scan_path(tmp_path)
        assert len(games) == 1
        assert games[0].folder_name == "RealGame"

    def test_scan_path_returns_empty_for_missing_root(self, tmp_path: Path):
        from backend.services.specialk_import_service import scan_path

        assert scan_path(tmp_path / "does-not-exist") == []

    def test_scan_path_returns_empty_for_file_not_dir(self, tmp_path: Path):
        from backend.services.specialk_import_service import scan_path

        f = tmp_path / "not-a-dir.txt"
        f.write_text("hello")
        assert scan_path(f) == []
