"""Phase 5 E2E tests: Metadata Fetching & Timeline.

Tests the timeline service (date grouping, pagination, filtering)
and metadata service (Steam Store API integration, cover download).
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from io import BytesIO

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["GAMEVAULT_SECRET_KEY"] = "test-secret"
os.environ["GAMEVAULT_DISABLE_AUTH"] = "true"

_tmp = tempfile.mkdtemp(prefix="gv_test5_")
os.environ["GAMEVAULT_DATA_DIR"] = _tmp
os.environ["GAMEVAULT_LIBRARY_DIR"] = str(Path(_tmp) / "library")
os.environ["GAMEVAULT_DB_PATH"] = str(Path(_tmp) / "test5.db")

from backend.config import Settings
import backend.config
backend.config.settings = Settings()
settings = backend.config.settings

from backend.database import get_db, init_db, close_db

from PIL import Image as PILImage


# ── Helpers ──────────────────────────────────────────────────────────────────

def create_test_image(width=800, height=600, color="blue") -> bytes:
    img = PILImage.new("RGB", (width, height), color)
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
    import backend.database as db_mod
    db_mod._db = None
    await init_db()


async def teardown_db():
    await close_db()


async def seed_data():
    """Create test games and screenshots for timeline testing."""
    from backend.services.game_service import create_game
    from backend.services.screenshot_service import create_screenshot
    from backend.services.image_processor import generate_thumbnails
    from backend.services.filesystem import get_screenshots_dir

    # Create games
    game1 = await create_game("Half-Life 2", steam_app_id=220)
    game2 = await create_game("Portal 2", steam_app_id=620)

    # Create screenshots on different dates for each game
    test_image = create_test_image(1920, 1080, "red")
    test_image2 = create_test_image(1920, 1080, "blue")
    test_image3 = create_test_image(1920, 1080, "green")

    screenshots_data = [
        (game1["id"], game1["folder_name"], "hl2_shot1.jpg", "2024-03-15T14:30:00", test_image),
        (game1["id"], game1["folder_name"], "hl2_shot2.jpg", "2024-03-15T16:00:00", test_image2),
        (game1["id"], game1["folder_name"], "hl2_shot3.jpg", "2024-03-20T10:00:00", test_image3),
        (game2["id"], game2["folder_name"], "portal_shot1.jpg", "2024-03-15T12:00:00", test_image),
        (game2["id"], game2["folder_name"], "portal_shot2.jpg", "2024-04-01T09:00:00", test_image2),
    ]

    for game_id, folder, filename, taken_at, img_data in screenshots_data:
        screenshots_dir = get_screenshots_dir(folder)
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        file_path = screenshots_dir / filename
        file_path.write_bytes(img_data)

        rel_path = f"{folder}/screenshots/{filename}"
        stem = Path(filename).stem
        thumb_sm, thumb_md = generate_thumbnails(file_path, folder, stem)

        from backend.services.image_processor import compute_sha256
        sha = compute_sha256(file_path)

        await create_screenshot(
            game_id=game_id,
            filename=filename,
            file_path=rel_path,
            source="upload",
            taken_at=taken_at,
            sha256_hash=sha,
            width=1920,
            height=1080,
            file_size=len(img_data),
            thumbnail_path_sm=thumb_sm,
            thumbnail_path_md=thumb_md,
        )

    return game1, game2


# ── Timeline Tests ───────────────────────────────────────────────────────────

async def test_timeline_global():
    print("\n[TEST] Test: Global timeline")
    from backend.services.timeline_service import get_timeline

    result = await get_timeline(page=1, limit=10)
    report("Returns days", len(result["days"]) > 0)
    report("Has total_days", result["total_days"] == 3, str(result["total_days"]))
    report("Days sorted newest first",
           result["days"][0]["date"] >= result["days"][-1]["date"])

    # Check day structure
    first_day = result["days"][0]
    report("Day has date", "date" in first_day)
    report("Day has screenshot_count", first_day["screenshot_count"] > 0)
    report("Day has games list", len(first_day["games"]) > 0)
    report("Day has screenshots array", len(first_day["screenshots"]) > 0)

    # Check screenshot structure
    shot = first_day["screenshots"][0]
    report("Screenshot has id", "id" in shot)
    report("Screenshot has game_name", "game_name" in shot)
    report("Screenshot has game_id", "game_id" in shot)
    report("Screenshot has thumbnail_path_sm", "thumbnail_path_sm" in shot)


async def test_timeline_game_filter(game1, game2):
    print("\n[TEST] Test: Timeline with game filter")
    from backend.services.timeline_service import get_timeline

    # Filter to Half-Life 2 only
    result = await get_timeline(game_id=game1["id"], page=1, limit=10)
    report("Returns days for HL2", len(result["days"]) > 0)

    # HL2 has screenshots on 2 different dates
    report("HL2 has 2 days", result["total_days"] == 2, str(result["total_days"]))

    # All screenshots should be from HL2
    for day in result["days"]:
        for shot in day["screenshots"]:
            if shot["game_id"] != game1["id"]:
                report("All screenshots belong to filtered game", False, f"got game_id={shot['game_id']}")
                return
    report("All screenshots belong to filtered game", True)


async def test_timeline_date_grouping():
    print("\n[TEST] Test: Timeline date grouping")
    from backend.services.timeline_service import get_timeline

    result = await get_timeline(page=1, limit=10)

    # Find March 15 day — should have 3 screenshots (2 HL2 + 1 Portal)
    march_15 = None
    for day in result["days"]:
        if day["date"] == "2024-03-15":
            march_15 = day
            break

    report("March 15 day found", march_15 is not None)
    if march_15:
        report("March 15 has 3 screenshots", march_15["screenshot_count"] == 3,
               str(march_15["screenshot_count"]))
        report("March 15 has 2 games", len(march_15["games"]) == 2,
               str(march_15["games"]))


async def test_timeline_pagination():
    print("\n[TEST] Test: Timeline pagination")
    from backend.services.timeline_service import get_timeline

    # Get first page with limit=1 (1 day per page)
    result = await get_timeline(page=1, limit=1)
    report("Page 1 has 1 day", len(result["days"]) == 1)
    report("has_more is True", result["has_more"])
    report("total_days is 3", result["total_days"] == 3)

    # Get page 2
    result2 = await get_timeline(page=2, limit=1)
    report("Page 2 has 1 day", len(result2["days"]) == 1)
    report("Page 2 has different date",
           result2["days"][0]["date"] != result["days"][0]["date"])


async def test_timeline_stats():
    print("\n[TEST] Test: Timeline stats")
    from backend.services.timeline_service import get_timeline_stats

    stats = await get_timeline_stats()
    report("total_screenshots = 5", stats["total_screenshots"] == 5, str(stats["total_screenshots"]))
    report("total_days = 3", stats["total_days"] == 3, str(stats["total_days"]))
    report("total_games = 2", stats["total_games"] == 2, str(stats["total_games"]))
    report("Has earliest_date", stats["earliest_date"] is not None)
    report("Has latest_date", stats["latest_date"] is not None)


async def test_timeline_empty():
    print("\n[TEST] Test: Timeline with no results")
    from backend.services.timeline_service import get_timeline

    # Filter to a non-existent game
    result = await get_timeline(game_id=99999, page=1, limit=10)
    report("Returns empty days", len(result["days"]) == 0)
    report("total_days = 0", result["total_days"] == 0)
    report("has_more = False", not result["has_more"])


# ── Metadata Tests ───────────────────────────────────────────────────────────

async def test_metadata_steam_store_api(game1):
    print("\n[TEST] Test: Metadata from Steam Store API")
    from backend.services.metadata_service import fetch_steam_metadata

    # Mock the HTTP call
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "220": {
            "success": True,
            "data": {
                "name": "Half-Life 2",
                "short_description": "A first-person shooter game.",
                "developers": ["Valve"],
                "publishers": ["Valve"],
                "genres": [{"description": "Action"}, {"description": "FPS"}],
                "release_date": {"coming_soon": False, "date": "Nov 16, 2004"},
                "header_image": "https://cdn.akamai.steamstatic.com/steam/apps/220/header.jpg",
            }
        }
    }

    import httpx
    with patch("backend.services.metadata_service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await fetch_steam_metadata(220)

    report("Returns data", result is not None)
    if result:
        report("Has name", result.get("name") == "Half-Life 2")
        report("Has description", "first-person" in (result.get("description") or "").lower())
        report("Has developer", result.get("developer") == "Valve")
        report("Has publisher", result.get("publisher") == "Valve")
        report("Has genres", "Action" in (result.get("genres") or ""))
        report("Has release_date", result.get("release_date") == "Nov 16, 2004")
        report("Has header_image_url", result.get("header_image_url") is not None)


async def test_metadata_no_steam_id():
    print("\n[TEST] Test: Steam metadata with no app_id")
    from backend.services.metadata_service import fetch_steam_metadata

    result = await fetch_steam_metadata(0)
    report("Returns None for app_id=0", result is None)


async def test_fetch_and_apply_metadata(game1):
    print("\n[TEST] Test: Fetch and apply metadata to game")
    from backend.services.metadata_service import fetch_and_apply_metadata
    from backend.services.game_service import get_game

    # Mock Steam API
    mock_steam_resp = MagicMock()
    mock_steam_resp.status_code = 200
    mock_steam_resp.json.return_value = {
        "220": {
            "success": True,
            "data": {
                "name": "Half-Life 2",
                "short_description": "Gordon Freeman returns.",
                "developers": ["Valve"],
                "publishers": ["Valve"],
                "genres": [{"description": "Action"}],
                "release_date": {"coming_soon": False, "date": "Nov 16, 2004"},
                "header_image": "https://cdn.akamai.steamstatic.com/steam/apps/220/header.jpg",
            }
        }
    }

    # Mock cover image download
    mock_cover_resp = MagicMock()
    mock_cover_resp.status_code = 200
    mock_cover_resp.content = create_test_image(300, 450, "orange")
    mock_cover_resp.headers = {"content-type": "image/jpeg"}

    with patch("backend.services.metadata_service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=[mock_steam_resp, mock_cover_resp])
        mock_client_cls.return_value = mock_client

        result = await fetch_and_apply_metadata(game1["id"])

    report("Result has game_id", result["game_id"] == game1["id"])
    report("Steam in sources_tried", "steam" in result["sources_tried"])
    report("Steam in sources_succeeded", "steam" in result["sources_succeeded"])
    report("Fields were updated", len(result["fields_updated"]) > 0)

    # Verify game was updated in DB
    game = await get_game(game1["id"])
    report("Game description updated", game["description"] is not None and len(game["description"]) > 0)
    report("Game developer updated", game["developer"] == "Valve")
    report("Game genres updated", game["genres"] is not None)


async def test_search_external_games():
    print("\n[TEST] Test: Search external games")
    from backend.services.metadata_service import search_external_games

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "items": [
            {"id": 730, "name": "Counter-Strike 2", "tiny_image": "https://example.com/cs2.jpg"},
            {"id": 440, "name": "Team Fortress 2", "tiny_image": "https://example.com/tf2.jpg"},
        ]
    }

    with patch("backend.services.metadata_service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        results = await search_external_games("Counter-Strike")

    report("Returns results", len(results) > 0)
    report("First result is CS2", results[0]["name"] == "Counter-Strike 2")
    report("Has steam_app_id", results[0]["steam_app_id"] == 730)
    report("Has cover_url", results[0]["cover_url"] is not None)
    report("Source is steam", results[0]["source"] == "steam")


async def test_metadata_nonexistent_game():
    print("\n[TEST] Test: Fetch metadata for non-existent game")
    from backend.services.metadata_service import fetch_and_apply_metadata

    try:
        await fetch_and_apply_metadata(99999)
        report("Raises ValueError", False, "No exception raised")
    except ValueError:
        report("Raises ValueError", True)


# ── Runner ───────────────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("Phase 5: Metadata & Timeline -- E2E Tests")
    print("=" * 60)

    await setup_db()

    try:
        game1, game2 = await seed_data()

        # Timeline tests
        await test_timeline_global()
        await test_timeline_game_filter(game1, game2)
        await test_timeline_date_grouping()
        await test_timeline_pagination()
        await test_timeline_stats()
        await test_timeline_empty()

        # Metadata tests
        await test_metadata_steam_store_api(game1)
        await test_metadata_no_steam_id()
        await test_fetch_and_apply_metadata(game1)
        await test_search_external_games()
        await test_metadata_nonexistent_game()
    finally:
        await teardown_db()

    print("\n" + "=" * 60)
    print(f"Results: {PASSED} passed, {FAILED} failed, {PASSED + FAILED} total")
    print("=" * 60)

    if FAILED > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
