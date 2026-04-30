"""Tests for the tone-map settings cache (perf fix called out in the
post-Phase-3 review). Previously every thumbnail re-opened a SQLite
connection to read two settings rows; now the values are cached at
module level and invalidated when the settings router writes new ones.
"""

from __future__ import annotations

import pytest


class TestToneMapCache:
    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        """Each test starts with a fresh, unpopulated cache."""
        from backend.services import image_processor

        image_processor._tone_map_cache = None
        yield
        image_processor._tone_map_cache = None

    def test_first_call_populates_cache(self, monkeypatch):
        from backend.services import image_processor

        load_calls = {"n": 0}

        def fake_load():
            load_calls["n"] += 1
            return {"algorithm": "aces", "exposure": 1.5}

        monkeypatch.setattr(image_processor, "_load_tone_map_settings", fake_load)

        algo, exposure = image_processor._get_tone_map_settings()
        assert (algo, exposure) == ("aces", 1.5)
        assert load_calls["n"] == 1
        assert image_processor._tone_map_cache is not None

    def test_subsequent_calls_use_cache(self, monkeypatch):
        from backend.services import image_processor

        load_calls = {"n": 0}

        def fake_load():
            load_calls["n"] += 1
            return {"algorithm": "reinhard", "exposure": 1.0}

        monkeypatch.setattr(image_processor, "_load_tone_map_settings", fake_load)

        # 200 generations (worst case: 100 screenshots * 2 thumbnail sizes)
        for _ in range(200):
            image_processor._get_tone_map_settings()

        # Loader called ONCE — that's the whole point of the cache
        assert load_calls["n"] == 1

    def test_invalidate_forces_reload(self, monkeypatch):
        from backend.services import image_processor

        values = iter([
            {"algorithm": "reinhard", "exposure": 1.0},
            {"algorithm": "aces", "exposure": 2.0},
        ])

        def fake_load():
            return next(values)

        monkeypatch.setattr(image_processor, "_load_tone_map_settings", fake_load)

        # First call: populates from "reinhard, 1.0"
        assert image_processor._get_tone_map_settings() == ("reinhard", 1.0)

        # Settings change → router calls invalidate
        image_processor.invalidate_tone_map_cache()
        assert image_processor._tone_map_cache is None

        # Next call re-reads → "aces, 2.0"
        assert image_processor._get_tone_map_settings() == ("aces", 2.0)

    def test_load_clamps_exposure_to_safe_range(self, monkeypatch, tmp_path):
        """Sanity check: bad/extreme values in app_settings get clamped on
        load rather than handed straight to numpy where they'd produce
        NaN/inf."""
        import sqlite3

        from backend.config import settings as app_settings
        from backend.services import image_processor

        # Build a real sqlite file with extreme + invalid settings
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO app_settings VALUES ('tone_map_algorithm', 'aces')"
        )
        conn.execute(
            # Way out of bounds; must be clamped to <= 8.0
            "INSERT INTO app_settings VALUES ('tone_map_exposure', '999999')"
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(app_settings, "db_path", db_path)

        out = image_processor._load_tone_map_settings()
        assert out["algorithm"] == "aces"
        assert out["exposure"] == 8.0  # clamped at the upper bound

    def test_load_falls_back_on_unknown_algorithm(self, monkeypatch, tmp_path):
        import sqlite3

        from backend.config import settings as app_settings
        from backend.services import image_processor

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        # Garbage algorithm — must NOT propagate; defaults to reinhard
        conn.execute(
            "INSERT INTO app_settings VALUES ('tone_map_algorithm', 'evil-algo')"
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(app_settings, "db_path", db_path)

        out = image_processor._load_tone_map_settings()
        assert out["algorithm"] == "reinhard"

    def test_load_handles_missing_db_gracefully(self, monkeypatch, tmp_path):
        from backend.config import settings as app_settings
        from backend.services import image_processor

        # Point at a nonexistent file
        monkeypatch.setattr(app_settings, "db_path", tmp_path / "does_not_exist.db")

        out = image_processor._load_tone_map_settings()
        # Defaults preserved, no exception
        assert out["algorithm"] == "reinhard"
        assert out["exposure"] == 1.0
