"""Regressions for the bugs found in the 2026-11 project audit."""

import re

import httpx
import pytest
from PIL import Image


@pytest.fixture
async def library(monkeypatch, tmp_path):
    from backend.config import settings
    from backend.database import close_db, init_db

    await close_db()
    for field, path in {"data_dir": tmp_path, "db_path": tmp_path / "state.db",
                        "library_dir": tmp_path / "library", "thumbnail_dir": tmp_path / "cache"}.items():
        monkeypatch.setattr(settings, field, path)
    await init_db()
    yield settings
    await close_db()


def _client() -> httpx.AsyncClient:
    from backend.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _add_screenshot(settings, game_name: str, filename: str = "a.png") -> tuple[dict, dict]:
    from backend.services import game_service, screenshot_service

    game = await game_service.get_or_create_game(game_name)
    path = settings.library_dir / game["folder_name"] / "screenshots" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 36), "red").save(path)
    shot = await screenshot_service.create_screenshot(
        game["id"], filename, f"{game['folder_name']}/screenshots/{filename}", width=64, height=36,
    )
    return game, shot


async def _search_total(query: str) -> int:
    from backend.services.search_service import search_screenshots

    _, total = await search_screenshots(query)
    return total


# ── Full-resolution view with a password set ────────────────────────────────


async def test_full_view_accepts_the_query_token(library, monkeypatch):
    """<img> can't send a header, so /full has to accept ?token= like /preview does.

    Without it every signed-in viewer got 401 and stayed on the 1920px preview.
    """
    from backend import auth

    _, shot = await _add_screenshot(library, "Token Game")
    monkeypatch.setattr(library, "disable_auth", False)
    await auth.set_password("correct horse")
    token = auth.create_access_token(password_hash=await auth.get_password_hash())

    async with _client() as client:
        for kind in ("image", "full"):
            url = f"/api/screenshots/{shot['id']}/{kind}"
            assert (await client.get(url)).status_code == 401
            assert (await client.get(f"{url}?token=not-a-token")).status_code == 401
            response = await client.get(f"{url}?token={token}")
            assert response.status_code == 200, kind
            assert response.headers["content-type"] == "image/png"


def test_full_renders_are_timed_as_their_own_media_kind():
    from backend.services.performance_diagnostics import _is_media_path, _media_operation

    assert _is_media_path("/api/screenshots/7/full")
    assert _media_operation("/api/screenshots/7/full") == "private.full"
    assert _media_operation("/api/gallery/screenshots/7/full") == "public.full"
    assert _media_operation("/api/screenshots/7/image") == "private.original"


# ── Renaming and deleting games ──────────────────────────────────────────────


async def test_renaming_a_game_updates_search(library):
    game, _ = await _add_screenshot(library, "App 12345")
    assert await _search_total("App") == 1

    async with _client() as client:
        response = await client.put(f"/api/games/{game['id']}", json={"name": "  Hollow Knight "})
    assert response.status_code == 200
    assert response.json()["name"] == "Hollow Knight"
    assert await _search_total("Hollow") == 1
    assert await _search_total("App") == 0


async def test_renaming_to_another_games_name_is_a_conflict(library):
    from backend.services import game_service

    alpha = await game_service.create_game("Alpha")
    await game_service.create_game("Beta")
    async with _client() as client:
        assert (await client.put(f"/api/games/{alpha['id']}", json={"name": "Beta"})).status_code == 409
        # Names match case-insensitively everywhere else, so "beta" collides too.
        assert (await client.put(f"/api/games/{alpha['id']}", json={"name": "beta"})).status_code == 409
        # Changing only the case of a game's own name is fine.
        assert (await client.put(f"/api/games/{alpha['id']}", json={"name": "ALPHA"})).status_code == 200
        for blank in ("", "   "):
            assert (await client.put(f"/api/games/{alpha['id']}", json={"name": blank})).status_code == 422
    assert (await game_service.get_game(alpha["id"]))["name"] == "ALPHA"


async def test_placeholder_keeps_its_name_when_the_real_one_is_taken(library, monkeypatch):
    """The sync app resolves games through this route; a 500 here stalled its uploads."""
    from backend.services import game_service, metadata_service

    await game_service.create_game("Hollow Knight")
    placeholder = await game_service.create_game("App 367520", steam_app_id=367520)

    async def steam_metadata(_app_id):
        return {"name": "Hollow Knight"}

    monkeypatch.setattr(metadata_service, "fetch_steam_metadata", steam_metadata)
    async with _client() as client:
        response = await client.get("/api/games/by-steam-appid/367520")
    assert response.status_code == 200
    assert response.json()["id"] == placeholder["id"]
    assert response.json()["name"] == "App 367520"


async def test_deleting_a_game_removes_its_search_entries(library):
    from backend.database import get_db
    from backend.services import game_service

    game, _ = await _add_screenshot(library, "Gone")
    await _add_screenshot(library, "Kept")
    await game_service.delete_game(game["id"])

    db = await get_db()
    cursor = await db.execute("SELECT game_name FROM screenshots_fts_content")
    assert [row[0] for row in await cursor.fetchall()] == ["Kept"]
    assert await _search_total("Gone") == 0
    assert await _search_total("Kept") == 1


# ── Share links ──────────────────────────────────────────────────────────────


async def test_one_share_page_view_counts_once(library):
    from backend.services import share_service

    _, shot = await _add_screenshot(library, "Share Game")
    link = await share_service.create_share_link(shot["id"])

    async with _client() as client:
        page = await client.get(f"/share/{link['token']}")
        image = await client.get(f"/share/{link['token']}/image")
    assert page.status_code == 200 and image.status_code == 200
    # The page always loads its image; that used to count the view twice.
    assert (await share_service.get_active_share_link(shot["id"]))["view_count"] == 1

    assert 'href="https://github.com/adiuv0/gamevault"' in page.text
    assert re.findall(r'og:image:(width|height)" content="(\d+)"', page.text) == [("width", "64"), ("height", "36")]


async def test_share_page_omits_an_unknown_image_size(library):
    from backend.database import get_db
    from backend.services import share_service

    _, shot = await _add_screenshot(library, "Share Game")
    db = await get_db()
    await db.execute("UPDATE screenshots SET width = NULL, height = NULL WHERE id = ?", (shot["id"],))
    await db.commit()
    link = await share_service.create_share_link(shot["id"])

    async with _client() as client:
        page = await client.get(f"/share/{link['token']}")
    assert page.status_code == 200
    assert "og:image:width" not in page.text
    assert "None" not in page.text


async def test_share_expiry_must_not_be_negative(library):
    _, shot = await _add_screenshot(library, "Share Game")
    async with _client() as client:
        assert (await client.post(f"/api/screenshots/{shot['id']}/share?expires_in_days=-1")).status_code == 422
        assert (await client.post(f"/api/screenshots/{shot['id']}/share?expires_in_days=7")).status_code == 200


# ── Smaller route fixes ──────────────────────────────────────────────────────


async def test_png_cover_is_served_as_png(library):
    from backend.services import game_service

    game = await game_service.create_game("Cover Game")
    image = library.library_dir / game["folder_name"] / "source.png"
    Image.new("RGB", (10, 15), "blue").save(image)
    await game_service.save_cover_image(game["id"], image.read_bytes(), "cover.png")

    async with _client() as client:
        for url in (f"/api/games/{game['id']}/cover", f"/api/gallery/games/{game['id']}/cover"):
            response = await client.get(url)
            assert response.status_code == 200
            assert response.headers["content-type"] == "image/png"


@pytest.mark.parametrize("url", [
    "/api/timeline?limit=0",
    "/api/timeline?limit=-1",
    "/api/timeline?page=0",
    "/api/search?q=a&limit=-5",
    "/api/search?q=a&page=0",
])
async def test_paging_rejects_zero_and_negative_values(library, url):
    async with _client() as client:
        assert (await client.get(url)).status_code == 422


async def test_oversized_page_sizes_are_capped(library):
    async with _client() as client:
        assert (await client.get("/api/search?q=a&limit=100000")).json()["limit"] == 100
        assert (await client.get("/api/timeline?limit=100000")).status_code == 200


async def test_settings_counts_special_k_imports_too(library):
    from backend.database import get_db

    db = await get_db()
    await db.execute("INSERT INTO steam_import_sessions (steam_user_id) VALUES ('someone')")
    await db.execute("INSERT INTO specialk_import_sessions (scan_path) VALUES ('C:/SK')")
    await db.commit()
    async with _client() as client:
        assert (await client.get("/api/settings")).json()["import_session_count"] == 2
