"""Phase 4 E2E tests: Steam Import pipeline.

Tests the complete import pipeline by mocking the HTTP layer
(since we can't hit Steam's actual servers in tests).
Verifies: session creation, import orchestration, progress events,
dedup by steam_id, dedup by hash, cancel, DB records, file creation.
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from io import BytesIO

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set up test env before importing app modules
os.environ["GAMEVAULT_SECRET_KEY"] = "test-secret"
os.environ["GAMEVAULT_DISABLE_AUTH"] = "true"

import aiosqlite
from PIL import Image

# Create temp dirs for test
_tmp = tempfile.mkdtemp(prefix="gv_test_")
os.environ["GAMEVAULT_DATA_DIR"] = _tmp
os.environ["GAMEVAULT_LIBRARY_DIR"] = str(Path(_tmp) / "library")
os.environ["GAMEVAULT_DB_PATH"] = str(Path(_tmp) / "test.db")

# Re-import settings to pick up new env
from backend.config import Settings
import backend.config
backend.config.settings = Settings()
settings = backend.config.settings

from backend.database import get_db, init_db, close_db

# Import services under test
from backend.services.steam_import_service import (
    create_import_session,
    get_import_session,
    update_import_session,
    get_progress_queue,
    cleanup_session,
    request_cancel,
    is_cancelled,
    run_import,
    emit_progress,
)
from backend.services.steam_scraper import (
    SteamScraper,
    SteamProfile,
    SteamScreenshot,
    SteamGameScreenshots,
)
from backend.services.game_service import get_or_create_game, list_games
from backend.services.screenshot_service import (
    list_screenshots,
    check_duplicate_steam_id,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def create_test_image(width=800, height=600, color="blue") -> bytes:
    """Create a valid JPEG image in memory."""
    img = Image.new("RGB", (width, height), color)
    buf = BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


PASSED = 0
FAILED = 0


def report(name: str, success: bool, detail: str = ""):
    global PASSED, FAILED
    if success:
        PASSED += 1
        print(f"  PASS: {name}")
    else:
        FAILED += 1
        print(f"  FAIL: {name}{f': {detail}' if detail else ''}")


async def setup_db():
    """Initialize a fresh test database."""
    # Reset DB connection
    import backend.database as db_mod
    db_mod._db = None
    await init_db()


async def teardown_db():
    """Close test database."""
    await close_db()


# ── Test: Session CRUD ───────────────────────────────────────────────────────

async def test_session_crud():
    print("\n[TEST] Test: Import session CRUD")

    # Create session
    sid = await create_import_session("testuser123")
    report("Create session", sid is not None and sid > 0, f"id={sid}")

    # Get session
    session = await get_import_session(sid)
    report("Get session", session is not None and session["status"] == "pending")

    # Update session
    await update_import_session(sid, status="running", total_games=5)
    session = await get_import_session(sid)
    report("Update session", session["status"] == "running" and session["total_games"] == 5)

    # Non-existent session
    none_session = await get_import_session(99999)
    report("Non-existent session returns None", none_session is None)


# ── Test: Progress queue & cancel ────────────────────────────────────────────

async def test_progress_queue():
    print("\n[TEST] Test: Progress queue and cancel flag")

    queue = get_progress_queue(999)
    report("Queue created", queue is not None)

    await emit_progress(999, "test_event", {"foo": "bar"})
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    report("Event received", event["event"] == "test_event" and event["data"]["foo"] == "bar")

    # Cancel flag
    report("Not cancelled initially", not is_cancelled(999))
    request_cancel(999)
    report("Cancelled after request", is_cancelled(999))

    cleanup_session(999)
    import backend.services.steam_import_service as sis_mod
    report("Cleanup removes queue", 999 not in sis_mod._progress_queues)


# ── Test: Full import pipeline with mocked scraper ───────────────────────────

async def test_full_import_pipeline():
    print("\n[TEST] Test: Full import pipeline (mocked HTTP)")

    test_image_data = create_test_image(1920, 1080, "red")
    test_image_data_2 = create_test_image(1920, 1080, "green")

    # Mock Steam scraper methods
    mock_profile = SteamProfile(
        user_id="test_user",
        profile_name="Test Player",
        avatar_url="https://example.com/avatar.jpg",
        is_numeric_id=False,
        profile_url="https://steamcommunity.com/id/test_user",
    )

    mock_screenshots = [
        SteamScreenshot(
            screenshot_id="100001",
            detail_url="/shared/filedetails/?id=100001",
            thumbnail_url="https://steamuserimages-a.akamaihd.net/ugc/test1/?imw=200",
            full_image_url="https://steamuserimages-a.akamaihd.net/ugc/test1/",
            description="A cool screenshot",
            date_taken="2024-06-15T14:30:00",
        ),
        SteamScreenshot(
            screenshot_id="100002",
            detail_url="/shared/filedetails/?id=100002",
            thumbnail_url="https://steamuserimages-a.akamaihd.net/ugc/test2/?imw=200",
            full_image_url="https://steamuserimages-a.akamaihd.net/ugc/test2/",
            description="Another screenshot",
            date_taken="2024-06-16T10:00:00",
        ),
    ]

    mock_game = SteamGameScreenshots(
        app_id=730,
        name="Counter-Strike 2",
        screenshot_count=2,
    )

    # Create session
    sid = await create_import_session("test_user")

    # Collect SSE events
    events = []
    queue = get_progress_queue(sid)

    async def collect_events():
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=10.0)
                events.append(event)
                if event["event"] == "done":
                    break
            except asyncio.TimeoutError:
                break

    with patch.object(SteamScraper, '__aenter__', return_value=AsyncMock(spec=SteamScraper)) as mock_enter:
        mock_scraper = mock_enter.return_value
        mock_scraper.validate_profile = AsyncMock(return_value=mock_profile)
        mock_scraper.discover_games = AsyncMock(return_value=[mock_game])
        mock_scraper.scrape_game_screenshots = AsyncMock(return_value=mock_screenshots)
        mock_scraper.get_screenshot_details = AsyncMock(side_effect=lambda s: s)

        # Return different images for each screenshot
        call_count = 0
        async def mock_download(url):
            nonlocal call_count
            call_count += 1
            return test_image_data if call_count == 1 else test_image_data_2

        mock_scraper.download_image = AsyncMock(side_effect=mock_download)

        with patch.object(SteamScraper, '__aexit__', return_value=AsyncMock()):
            # Run import + collect events concurrently
            await asyncio.gather(
                run_import(
                    session_id=sid,
                    user_id="test_user",
                    steam_login_secure="",
                    session_id_cookie="",
                    game_ids=None,
                    is_numeric_id=False,
                ),
                collect_events(),
            )

    # Verify session final state
    session = await get_import_session(sid)
    report("Session status is completed", session["status"] == "completed", session["status"])
    report("Completed screenshots = 2", session["completed_screenshots"] == 2, str(session["completed_screenshots"]))
    report("Skipped screenshots = 0", session["skipped_screenshots"] == 0)
    report("Failed screenshots = 0", session["failed_screenshots"] == 0)

    # Verify SSE events
    event_types = [e["event"] for e in events]
    report("Has import_started event", "import_started" in event_types)
    report("Has games_discovered event", "games_discovered" in event_types)
    report("Has game_start event", "game_start" in event_types)
    report("Has screenshot_complete events", event_types.count("screenshot_complete") == 2,
           f"count={event_types.count('screenshot_complete')}")
    report("Has game_complete event", "game_complete" in event_types)
    report("Has import_complete event", "import_complete" in event_types)
    report("Has done event", "done" in event_types)

    # Verify game was created
    games = await list_games()
    cs2_games = [g for g in games if g["name"] == "Counter-Strike 2"]
    report("Game 'Counter-Strike 2' created in DB", len(cs2_games) == 1)
    if cs2_games:
        report("Game has steam_app_id=730", cs2_games[0]["steam_app_id"] == 730)
        report("Game screenshot_count=2", cs2_games[0]["screenshot_count"] == 2)

    # Verify screenshots in DB
    screenshots, total = await list_screenshots(cs2_games[0]["id"])
    report("2 screenshots in DB", total == 2, str(total))
    if total == 2:
        report("Screenshot source = steam_import", screenshots[0]["source"] == "steam_import")
        report("Screenshot has steam_screenshot_id", screenshots[0]["steam_screenshot_id"] is not None)
        report("Screenshot has sha256_hash", screenshots[0]["sha256_hash"] is not None)
        report("Screenshot has thumbnails", screenshots[0]["thumbnail_path_sm"] is not None)

    # Verify files on disk
    game_dir = settings.library_dir / "Counter-Strike 2"
    screenshots_dir = game_dir / "screenshots"
    report("Game directory created", game_dir.exists())
    report("Screenshots directory exists", screenshots_dir.exists())
    files = list(screenshots_dir.glob("steam_*"))
    report("2 screenshot files on disk", len(files) == 2, str([f.name for f in files]))


# ── Test: Dedup by Steam ID ─────────────────────────────────────────────────

async def test_dedup_steam_id():
    print("\n[TEST] Test: Deduplication by Steam screenshot ID")

    # The screenshots from the previous test should still be in DB
    is_dup = await check_duplicate_steam_id("100001")
    report("Existing steam ID detected as duplicate", is_dup)

    is_not_dup = await check_duplicate_steam_id("999999")
    report("New steam ID not a duplicate", not is_not_dup)


# ── Test: Re-import skips duplicates ─────────────────────────────────────────

async def test_reimport_skips_duplicates():
    print("\n[TEST] Test: Re-import skips already imported screenshots")

    test_image_data = create_test_image(1920, 1080, "red")

    mock_profile = SteamProfile(
        user_id="test_user",
        profile_name="Test Player",
        is_numeric_id=False,
        profile_url="https://steamcommunity.com/id/test_user",
    )

    # Same screenshots as before — should all be skipped
    mock_screenshots = [
        SteamScreenshot(
            screenshot_id="100001",
            detail_url="/shared/filedetails/?id=100001",
            thumbnail_url="https://steamuserimages-a.akamaihd.net/ugc/test1/?imw=200",
            full_image_url="https://steamuserimages-a.akamaihd.net/ugc/test1/",
        ),
    ]

    mock_game = SteamGameScreenshots(
        app_id=730,
        name="Counter-Strike 2",
        screenshot_count=1,
    )

    sid = await create_import_session("test_user")
    events = []
    queue = get_progress_queue(sid)

    async def collect_events():
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=10.0)
                events.append(event)
                if event["event"] == "done":
                    break
            except asyncio.TimeoutError:
                break

    with patch.object(SteamScraper, '__aenter__', return_value=AsyncMock(spec=SteamScraper)) as mock_enter:
        mock_scraper = mock_enter.return_value
        mock_scraper.validate_profile = AsyncMock(return_value=mock_profile)
        mock_scraper.discover_games = AsyncMock(return_value=[mock_game])
        mock_scraper.scrape_game_screenshots = AsyncMock(return_value=mock_screenshots)
        mock_scraper.get_screenshot_details = AsyncMock(side_effect=lambda s: s)
        mock_scraper.download_image = AsyncMock(return_value=test_image_data)

        with patch.object(SteamScraper, '__aexit__', return_value=AsyncMock()):
            await asyncio.gather(
                run_import(
                    session_id=sid,
                    user_id="test_user",
                    is_numeric_id=False,
                ),
                collect_events(),
            )

    session = await get_import_session(sid)
    report("Session completed", session["status"] == "completed")
    report("0 completed (all skipped)", session["completed_screenshots"] == 0)
    report("1 skipped", session["skipped_screenshots"] == 1)

    # Verify screenshot_skipped event was emitted
    skipped_events = [e for e in events if e["event"] == "screenshot_skipped"]
    report("screenshot_skipped event emitted", len(skipped_events) == 1)
    if skipped_events:
        report("Skip reason = already_imported", skipped_events[0]["data"]["reason"] == "already_imported")


# ── Test: Cancel import ──────────────────────────────────────────────────────

async def test_cancel_import():
    print("\n[TEST] Test: Cancel import")

    mock_profile = SteamProfile(
        user_id="test_user",
        profile_name="Test Player",
        is_numeric_id=False,
        profile_url="https://steamcommunity.com/id/test_user",
    )

    # Many games to give us time to cancel
    mock_games = [
        SteamGameScreenshots(app_id=i, name=f"Game {i}", screenshot_count=10)
        for i in range(1000, 1005)
    ]

    mock_screenshots = [
        SteamScreenshot(
            screenshot_id=f"cancel_{i}",
            detail_url=f"/shared/filedetails/?id=cancel_{i}",
            thumbnail_url=f"https://example.com/thumb_{i}.jpg",
            full_image_url=f"https://example.com/full_{i}.jpg",
        )
        for i in range(10)
    ]

    test_image = create_test_image(100, 100, "yellow")

    sid = await create_import_session("test_user")
    events = []
    queue = get_progress_queue(sid)

    async def collect_events():
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
                events.append(event)
                if event["event"] == "done":
                    break
                # Cancel after the first game_start event
                if event["event"] == "game_start":
                    request_cancel(sid)
            except asyncio.TimeoutError:
                break

    with patch.object(SteamScraper, '__aenter__', return_value=AsyncMock(spec=SteamScraper)) as mock_enter:
        mock_scraper = mock_enter.return_value
        mock_scraper.validate_profile = AsyncMock(return_value=mock_profile)
        mock_scraper.discover_games = AsyncMock(return_value=mock_games)
        mock_scraper.scrape_game_screenshots = AsyncMock(return_value=mock_screenshots)
        mock_scraper.get_screenshot_details = AsyncMock(side_effect=lambda s: s)
        mock_scraper.download_image = AsyncMock(return_value=test_image)

        with patch.object(SteamScraper, '__aexit__', return_value=AsyncMock()):
            await asyncio.gather(
                run_import(
                    session_id=sid,
                    user_id="test_user",
                    is_numeric_id=False,
                ),
                collect_events(),
            )

    session = await get_import_session(sid)
    report("Session status is cancelled", session["status"] == "cancelled", session["status"])

    event_types = [e["event"] for e in events]
    report("import_cancelled event emitted", "import_cancelled" in event_types)
    # Should NOT have completed all 5 games
    game_complete_count = event_types.count("game_complete")
    report("Not all games completed (cancelled early)", game_complete_count < 5, f"completed={game_complete_count}")


# ── Test: Import with game_ids filter ────────────────────────────────────────

async def test_import_with_game_filter():
    print("\n[TEST] Test: Import with game_ids filter")

    test_image = create_test_image(640, 480, "purple")

    mock_profile = SteamProfile(
        user_id="test_user",
        profile_name="Test Player",
        is_numeric_id=False,
        profile_url="https://steamcommunity.com/id/test_user",
    )

    mock_games = [
        SteamGameScreenshots(app_id=440, name="Team Fortress 2", screenshot_count=1),
        SteamGameScreenshots(app_id=570, name="Dota 2", screenshot_count=1),
    ]

    mock_screenshots_tf2 = [
        SteamScreenshot(
            screenshot_id="tf2_001",
            detail_url="/shared/filedetails/?id=tf2_001",
            thumbnail_url="https://example.com/tf2.jpg",
            full_image_url="https://example.com/tf2_full.jpg",
            description="TF2 screenshot",
        ),
    ]

    sid = await create_import_session("test_user")
    events = []
    queue = get_progress_queue(sid)

    async def collect_events():
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=10.0)
                events.append(event)
                if event["event"] == "done":
                    break
            except asyncio.TimeoutError:
                break

    with patch.object(SteamScraper, '__aenter__', return_value=AsyncMock(spec=SteamScraper)) as mock_enter:
        mock_scraper = mock_enter.return_value
        mock_scraper.validate_profile = AsyncMock(return_value=mock_profile)
        mock_scraper.discover_games = AsyncMock(return_value=mock_games)
        mock_scraper.scrape_game_screenshots = AsyncMock(return_value=mock_screenshots_tf2)
        mock_scraper.get_screenshot_details = AsyncMock(side_effect=lambda s: s)
        mock_scraper.download_image = AsyncMock(return_value=test_image)

        with patch.object(SteamScraper, '__aexit__', return_value=AsyncMock()):
            # Only import TF2 (app_id=440)
            await asyncio.gather(
                run_import(
                    session_id=sid,
                    user_id="test_user",
                    game_ids=[440],
                    is_numeric_id=False,
                ),
                collect_events(),
            )

    session = await get_import_session(sid)
    report("Session completed", session["status"] == "completed")
    report("Only 1 game imported", session["total_games"] == 1)
    report("1 screenshot completed", session["completed_screenshots"] == 1)

    # Verify only TF2 was created, not Dota 2
    games = await list_games()
    tf2 = [g for g in games if g["name"] == "Team Fortress 2"]
    dota = [g for g in games if g["name"] == "Dota 2"]
    report("TF2 game created", len(tf2) == 1)
    report("Dota 2 NOT created", len(dota) == 0)


# ── Test: Failed profile validation ──────────────────────────────────────────

async def test_failed_validation():
    print("\n[TEST] Test: Import with failed profile validation")

    sid = await create_import_session("bad_user")
    events = []
    queue = get_progress_queue(sid)

    async def collect_events():
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=10.0)
                events.append(event)
                if event["event"] == "done":
                    break
            except asyncio.TimeoutError:
                break

    with patch.object(SteamScraper, '__aenter__', return_value=AsyncMock(spec=SteamScraper)) as mock_enter:
        mock_scraper = mock_enter.return_value
        mock_scraper.validate_profile = AsyncMock(
            side_effect=ValueError("Steam profile not found or is private")
        )

        with patch.object(SteamScraper, '__aexit__', return_value=AsyncMock()):
            await asyncio.gather(
                run_import(
                    session_id=sid,
                    user_id="bad_user",
                    is_numeric_id=False,
                ),
                collect_events(),
            )

    session = await get_import_session(sid)
    report("Session status is failed", session["status"] == "failed")
    report("Error log has message", session["error_log"] is not None)

    event_types = [e["event"] for e in events]
    report("import_error event emitted", "import_error" in event_types)


# ── Test: Steam date parsing ────────────────────────────────────────────────

async def test_steam_date_parsing():
    print("\n[TEST] Test: Steam date format parsing")

    from backend.services.steam_scraper import _parse_steam_date

    d1 = _parse_steam_date("Jan 15, 2024 @ 3:42pm")
    report("Format: 'Jan 15, 2024 @ 3:42pm'", d1 is not None and d1.year == 2024 and d1.month == 1)

    d2 = _parse_steam_date("15 Jan, 2024 @ 3:42pm")
    report("Format: '15 Jan, 2024 @ 3:42pm'", d2 is not None and d2.day == 15)

    d3 = _parse_steam_date("Jun 01, 2023, 12:00pm")
    report("Format: 'Jun 01, 2023, 12:00pm'", d3 is not None and d3.year == 2023)

    d4 = _parse_steam_date("")
    report("Empty string returns None", d4 is None)

    d5 = _parse_steam_date("not a date")
    report("Invalid string returns None", d5 is None)


# ── Test: URL helper functions ───────────────────────────────────────────────

async def test_url_helpers():
    print("\n[TEST] Test: URL helper functions")

    from backend.services.steam_scraper import _extract_full_image_url, _get_profile_url

    # Full image URL extraction
    full = _extract_full_image_url("https://steamuserimages-a.akamaihd.net/ugc/123/abc/?imw=200")
    report("Removes query params from thumbnail URL",
           full == "https://steamuserimages-a.akamaihd.net/ugc/123/abc/")

    empty = _extract_full_image_url("")
    report("Empty URL returns empty", empty == "")

    # Profile URL building
    url_custom = _get_profile_url("myname")
    report("Custom URL format", url_custom == "https://steamcommunity.com/id/myname")

    url_numeric = _get_profile_url("76561198012345678")
    report("Numeric ID format", url_numeric == "https://steamcommunity.com/profiles/76561198012345678")

    url_forced = _get_profile_url("myname", is_numeric=True)
    report("Forced numeric format", url_forced == "https://steamcommunity.com/profiles/myname")


# ── Test: Extension guessing ────────────────────────────────────────────────

async def test_extension_guessing():
    print("\n[TEST] Test: File extension guessing")

    from backend.services.steam_import_service import _guess_extension

    # URL-based
    report("PNG from URL", _guess_extension("https://example.com/image.png", b"") == "png")
    report("JPG from URL", _guess_extension("https://example.com/image.jpg", b"") == "jpg")
    report("JPEG from URL", _guess_extension("https://example.com/image.jpeg", b"") == "jpg")
    report("WebP from URL", _guess_extension("https://example.com/image.webp", b"") == "webp")

    # Magic bytes
    png_magic = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    report("PNG from magic bytes", _guess_extension("https://example.com/noext", png_magic) == "png")

    jpg_magic = b"\xff\xd8\xff" + b"\x00" * 100
    report("JPG from magic bytes", _guess_extension("https://example.com/noext", jpg_magic) == "jpg")

    # Default
    report("Default to jpg", _guess_extension("https://example.com/noext", b"\x00" * 100) == "jpg")


# ── Runner ───────────────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("Phase 4: Steam Import — E2E Tests")
    print("=" * 60)

    await setup_db()

    try:
        await test_session_crud()
        await test_progress_queue()
        await test_steam_date_parsing()
        await test_url_helpers()
        await test_extension_guessing()
        await test_full_import_pipeline()
        await test_dedup_steam_id()
        await test_reimport_skips_duplicates()
        await test_cancel_import()
        await test_import_with_game_filter()
        await test_failed_validation()
    finally:
        await teardown_db()

    print("\n" + "=" * 60)
    print(f"Results: {PASSED} passed, {FAILED} failed, {PASSED + FAILED} total")
    print("=" * 60)

    if FAILED > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
