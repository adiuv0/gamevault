"""Tests for the Phase 3 security fixes from SECURITY_AUDIT_2026-04-27.

Covers:
  - GV-010: Special K path allowlist + refuse when auth disabled
  - GV-011: CLI keyring-backed token storage with JSON fallback
  - GV-013: SQL sort allowlists fall back safely on unknown values
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


# ── GV-010: Special K path allowlist ─────────────────────────────────────────


class TestSpecialKAllowedRoots:
    def test_no_allowlist_with_auth_enabled_accepts_any_path(
        self, tmp_path: Path, monkeypatch
    ):
        """Mode 2: empty allowlist + auth enabled → allow any path."""
        from backend.config import settings as app_settings
        from backend.routers import specialk_import

        monkeypatch.setattr(app_settings, "specialk_allowed_roots", "")
        monkeypatch.setattr(app_settings, "disable_auth", False)

        sub = tmp_path / "anywhere"
        sub.mkdir()

        result = specialk_import._enforce_allowed_root(str(sub))
        assert result == sub.resolve()

    def test_no_allowlist_with_auth_disabled_refuses(self, tmp_path: Path, monkeypatch):
        """Mode 3: auth disabled + no allowlist → 403, regardless of path."""
        from backend.config import settings as app_settings
        from backend.routers import specialk_import

        monkeypatch.setattr(app_settings, "specialk_allowed_roots", "")
        monkeypatch.setattr(app_settings, "disable_auth", True)

        with pytest.raises(HTTPException) as exc:
            specialk_import._enforce_allowed_root(str(tmp_path))
        assert exc.value.status_code == 403
        assert "ALLOWED_ROOTS" in exc.value.detail

    def test_allowlist_set_accepts_path_under_root(self, tmp_path: Path, monkeypatch):
        """Mode 1: allowlist set → path must resolve under one of the entries."""
        from backend.config import settings as app_settings
        from backend.routers import specialk_import

        allowed = tmp_path / "allowed"
        allowed.mkdir()
        sub = allowed / "GameA"
        sub.mkdir()

        monkeypatch.setattr(app_settings, "specialk_allowed_roots", str(allowed))
        monkeypatch.setattr(app_settings, "disable_auth", False)

        result = specialk_import._enforce_allowed_root(str(sub))
        assert result == sub.resolve()

    def test_allowlist_set_rejects_path_outside_root(self, tmp_path: Path, monkeypatch):
        from backend.config import settings as app_settings
        from backend.routers import specialk_import

        allowed = tmp_path / "allowed"
        allowed.mkdir()
        outside = tmp_path / "elsewhere"
        outside.mkdir()

        monkeypatch.setattr(app_settings, "specialk_allowed_roots", str(allowed))
        monkeypatch.setattr(app_settings, "disable_auth", False)

        with pytest.raises(HTTPException) as exc:
            specialk_import._enforce_allowed_root(str(outside))
        assert exc.value.status_code == 403

    def test_allowlist_rejects_traversal_attempt(self, tmp_path: Path, monkeypatch):
        """``allowed/../elsewhere`` must NOT be accepted just because the
        string starts with the allowed prefix — the path is resolved before
        the containment check."""
        from backend.config import settings as app_settings
        from backend.routers import specialk_import

        allowed = tmp_path / "allowed"
        allowed.mkdir()
        outside = tmp_path / "elsewhere"
        outside.mkdir()

        monkeypatch.setattr(app_settings, "specialk_allowed_roots", str(allowed))
        monkeypatch.setattr(app_settings, "disable_auth", False)

        traversal = f"{allowed}/../elsewhere"
        with pytest.raises(HTTPException) as exc:
            specialk_import._enforce_allowed_root(traversal)
        assert exc.value.status_code == 403

    def test_allowlist_supports_multiple_roots(self, tmp_path: Path, monkeypatch):
        from backend.config import settings as app_settings
        from backend.routers import specialk_import

        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        target_in_b = b / "Game"
        target_in_b.mkdir()

        monkeypatch.setattr(
            app_settings, "specialk_allowed_roots", f"{a},{b}"
        )
        monkeypatch.setattr(app_settings, "disable_auth", False)

        result = specialk_import._enforce_allowed_root(str(target_in_b))
        assert result == target_in_b.resolve()

    def test_allowlist_ignores_blank_entries(self, tmp_path: Path, monkeypatch):
        """Trailing/empty entries (`a, ,b`) shouldn't be treated as a root."""
        from backend.config import settings as app_settings
        from backend.routers import specialk_import

        a = tmp_path / "a"
        a.mkdir()
        target = a / "Game"
        target.mkdir()

        monkeypatch.setattr(
            app_settings, "specialk_allowed_roots", f" {a} , , "
        )
        monkeypatch.setattr(app_settings, "disable_auth", False)

        result = specialk_import._enforce_allowed_root(str(target))
        assert result == target.resolve()


# ── GV-013: SQL sort allowlists ──────────────────────────────────────────────


class TestSortAllowlists:
    def test_game_sort_clauses_known_values(self):
        from backend.services.game_service import (
            GAME_SORT_CLAUSES,
            GAME_DEFAULT_SORT,
        )
        assert set(GAME_SORT_CLAUSES) == {"name", "date", "count"}
        assert GAME_DEFAULT_SORT == GAME_SORT_CLAUSES["name"]

    def test_game_sort_unknown_falls_back(self):
        from backend.services.game_service import (
            GAME_SORT_CLAUSES,
            GAME_DEFAULT_SORT,
        )
        # Anything not in the allowlist must produce the default — not be
        # interpolated into SQL.
        for evil in (
            "name; DROP TABLE games",
            "; DELETE FROM screenshots",
            "1=1",
            "../etc/passwd",
            "name'--",
        ):
            assert GAME_SORT_CLAUSES.get(evil, GAME_DEFAULT_SORT) == GAME_DEFAULT_SORT

    def test_screenshot_sort_clauses_known_values(self):
        from backend.services.screenshot_service import (
            SCREENSHOT_SORT_CLAUSES,
            SCREENSHOT_DEFAULT_SORT,
        )
        assert set(SCREENSHOT_SORT_CLAUSES) == {"date_asc", "date_desc", "name"}
        assert SCREENSHOT_DEFAULT_SORT == SCREENSHOT_SORT_CLAUSES["date_desc"]

    def test_screenshot_sort_unknown_falls_back(self):
        from backend.services.screenshot_service import (
            SCREENSHOT_SORT_CLAUSES,
            SCREENSHOT_DEFAULT_SORT,
        )
        assert SCREENSHOT_SORT_CLAUSES.get("malicious; DROP", SCREENSHOT_DEFAULT_SORT) == SCREENSHOT_DEFAULT_SORT

    def test_search_sort_clauses_known_values(self):
        from backend.services.search_service import (
            SEARCH_SORT_CLAUSES,
            SEARCH_LIST_ALL_SORT_CLAUSES,
        )
        assert set(SEARCH_SORT_CLAUSES) == {"relevance", "date_asc", "date_desc"}
        assert set(SEARCH_LIST_ALL_SORT_CLAUSES) == {"date_asc", "date_desc"}

    def test_search_sort_unknown_falls_back_to_relevance(self):
        from backend.services.search_service import SEARCH_SORT_CLAUSES
        # The actual fallback in the search function is to
        # SEARCH_SORT_CLAUSES["relevance"]; verify the dict is well-formed
        assert SEARCH_SORT_CLAUSES.get("evil") is None
        assert "bm25" in SEARCH_SORT_CLAUSES["relevance"].lower()


# ── GV-011: CLI keyring storage ──────────────────────────────────────────────


@pytest.fixture
def cli_module(monkeypatch, tmp_path):
    """Import the CLI module fresh with a redirected CONFIG_PATH."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cli"))
    if "gamevault_sync" in sys.modules:
        del sys.modules["gamevault_sync"]
    import gamevault_sync as cli_mod  # type: ignore
    monkeypatch.setattr(cli_mod, "CONFIG_PATH", tmp_path / "test_sync.json")
    cli_mod._keyring_warning_shown = False
    return cli_mod


class _FakeKeyring:
    """Minimal in-memory stand-in for the `keyring` package."""

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, value):
        self.store[(service, username)] = value

    def get_password(self, service, username):
        return self.store.get((service, username))

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


class TestCliTokenStorage:
    def test_save_uses_keyring_when_available(self, cli_module, monkeypatch):
        fake = _FakeKeyring()
        monkeypatch.setattr(cli_module, "_get_keyring_module", lambda: fake)

        cli_module.save_config({
            "server": "http://x",
            "token": "secret-token-abc",
            "specialk_path": "C:/SK",
        })

        # Token NOT in JSON
        cfg_on_disk = json.loads(cli_module.CONFIG_PATH.read_text())
        assert "token" not in cfg_on_disk
        assert cfg_on_disk.get(cli_module._KEYRING_FLAG_KEY) is True
        # Token IS in keyring
        assert fake.store == {
            (cli_module._KEYRING_SERVICE, cli_module._KEYRING_USERNAME): "secret-token-abc"
        }

    def test_load_pulls_token_from_keyring(self, cli_module, monkeypatch):
        fake = _FakeKeyring()
        fake.set_password(
            cli_module._KEYRING_SERVICE,
            cli_module._KEYRING_USERNAME,
            "stored-token-xyz",
        )
        monkeypatch.setattr(cli_module, "_get_keyring_module", lambda: fake)

        # Pretend a previous save happened: only the flag is in the JSON
        cli_module.CONFIG_PATH.write_text(json.dumps({
            "server": "http://x",
            cli_module._KEYRING_FLAG_KEY: True,
        }))

        cfg = cli_module.load_config()
        assert cfg.get("token") == "stored-token-xyz"
        assert cfg.get("server") == "http://x"

    def test_save_falls_back_to_json_when_keyring_missing(
        self, cli_module, monkeypatch, capsys
    ):
        monkeypatch.setattr(cli_module, "_get_keyring_module", lambda: None)

        cli_module.save_config({
            "server": "http://x",
            "token": "fallback-token",
        })

        cfg_on_disk = json.loads(cli_module.CONFIG_PATH.read_text())
        assert cfg_on_disk.get("token") == "fallback-token"
        assert cli_module._KEYRING_FLAG_KEY not in cfg_on_disk
        # Warning printed to stderr
        captured = capsys.readouterr()
        assert "keyring" in captured.err.lower()

    def test_save_clears_keyring_when_token_empty(self, cli_module, monkeypatch):
        fake = _FakeKeyring()
        # Pre-existing token in keyring
        fake.set_password(
            cli_module._KEYRING_SERVICE,
            cli_module._KEYRING_USERNAME,
            "old-token",
        )
        monkeypatch.setattr(cli_module, "_get_keyring_module", lambda: fake)

        cli_module.save_config({"server": "http://x", "token": ""})

        # Both backends cleared
        assert fake.store == {}
        cfg_on_disk = json.loads(cli_module.CONFIG_PATH.read_text())
        assert "token" not in cfg_on_disk
        assert cli_module._KEYRING_FLAG_KEY not in cfg_on_disk

    def test_legacy_json_token_still_loads(self, cli_module, monkeypatch):
        """Backward compat: pre-keyring installs have token in JSON."""
        fake = _FakeKeyring()
        monkeypatch.setattr(cli_module, "_get_keyring_module", lambda: fake)

        cli_module.CONFIG_PATH.write_text(json.dumps({
            "server": "http://x",
            "token": "legacy-token",
        }))

        cfg = cli_module.load_config()
        assert cfg.get("token") == "legacy-token"
