"""Shared test fixtures for all test modules."""

import os
import tempfile
from pathlib import Path

import pytest

# ── Exclude legacy standalone runners from pytest collection ────────────────
# Phase 4 and 5 tests use asyncio.get_event_loop().run_until_complete() and
# manipulate global state (re-create Settings singleton, etc.). They conflict
# with pytest-asyncio's event loop management. Run them standalone:
#   python tests/test_phase4_steam_import.py
#   python tests/test_phase5_metadata_timeline.py
collect_ignore = [
    "test_phase4_steam_import.py",
    "test_phase5_metadata_timeline.py",
]

# ── Environment overrides (must be set before importing backend modules) ─────
_tmp = tempfile.mkdtemp(prefix="gv_pytest_")
os.environ["GAMEVAULT_DATA_DIR"] = _tmp
os.environ["GAMEVAULT_LIBRARY_DIR"] = os.path.join(_tmp, "library")
os.environ["GAMEVAULT_DB_PATH"] = os.path.join(_tmp, "test.db")
os.environ["GAMEVAULT_SECRET_KEY"] = "pytest-secret-key"
os.environ["GAMEVAULT_DISABLE_AUTH"] = "true"


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir():
    """Return the path to the test fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def steam_grid_html():
    """Return the Steam grid page HTML fixture."""
    return (FIXTURES_DIR / "steam_grid_page.html").read_text(encoding="utf-8")


@pytest.fixture
def steam_detail_html():
    """Return the Steam detail page HTML fixture."""
    return (FIXTURES_DIR / "steam_detail_page.html").read_text(encoding="utf-8")


@pytest.fixture
def steam_profile_html():
    """Return the Steam profile page HTML fixture."""
    return (FIXTURES_DIR / "steam_profile_page.html").read_text(encoding="utf-8")


@pytest.fixture
def steam_profile_error_html():
    """Return the Steam profile error page HTML fixture."""
    return (FIXTURES_DIR / "steam_profile_error.html").read_text(encoding="utf-8")


@pytest.fixture
def steam_detail_mature_html():
    """Return the Steam mature content detail page HTML fixture."""
    return (FIXTURES_DIR / "steam_detail_mature.html").read_text(encoding="utf-8")
