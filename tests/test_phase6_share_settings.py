"""Phase 6 Tests: Share links + Settings endpoint.

Converted to proper pytest-asyncio tests for compatibility with the test suite.
"""

import pytest

import backend.database as db_mod
from backend.config import settings


# ── Shared fixture: isolated DB for Phase 6 tests ──────────────────────────

@pytest.fixture(autouse=True, scope="module")
async def phase6_db(tmp_path_factory):
    """Initialize a fresh DB for the entire Phase 6 test module."""
    tmp = tmp_path_factory.mktemp("phase6")
    original_db_path = settings.db_path

    settings.db_path = tmp / "phase6_test.db"

    # Reset singleton
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
           VALUES ('Test Game', 'test-game', 2)"""
    )
    await db.execute(
        """INSERT INTO screenshots (game_id, filename, file_path, source, uploaded_at)
           VALUES (1, 'shot1.png', '/tmp/shot1.png', 'upload', '2024-01-15T10:00:00')"""
    )
    await db.execute(
        """INSERT INTO screenshots (game_id, filename, file_path, source, uploaded_at)
           VALUES (1, 'shot2.png', '/tmp/shot2.png', 'upload', '2024-01-16T11:00:00')"""
    )
    await db.execute(
        """INSERT INTO annotations (screenshot_id, content, content_html)
           VALUES (1, 'Test note', '<p>Test note</p>')"""
    )
    await db.commit()

    yield db

    await db_mod.close_db()
    settings.db_path = original_db_path


# ── Share Service Tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_share_link():
    from backend.services.share_service import create_share_link

    link = await create_share_link(1)
    assert link is not None
    assert "token" in link
    assert "url" in link
    assert link["screenshot_id"] == 1
    assert link["is_active"] == 1 or link["is_active"] is True


@pytest.mark.asyncio
async def test_create_share_link_returns_existing():
    from backend.services.share_service import create_share_link

    link1 = await create_share_link(1)
    link2 = await create_share_link(1)
    assert link1["id"] == link2["id"]
    assert link1["token"] == link2["token"]


@pytest.mark.asyncio
async def test_get_share_link_by_token():
    from backend.services.share_service import create_share_link, get_share_link_by_token

    link = await create_share_link(1)
    fetched = await get_share_link_by_token(link["token"])
    assert fetched is not None
    assert fetched["id"] == link["id"]


@pytest.mark.asyncio
async def test_get_share_link_by_invalid_token():
    from backend.services.share_service import get_share_link_by_token

    result = await get_share_link_by_token("nonexistent-token-xyz")
    assert result is None


@pytest.mark.asyncio
async def test_get_active_share_link():
    from backend.services.share_service import get_active_share_link

    link = await get_active_share_link(1)
    assert link is not None

    link2 = await get_active_share_link(2)
    assert link2 is None


@pytest.mark.asyncio
async def test_deactivate_share_link():
    from backend.services.share_service import (
        create_share_link,
        deactivate_share_link,
        get_active_share_link,
        get_share_link_by_token,
    )

    link = await create_share_link(2)
    assert link is not None

    result = await deactivate_share_link(link["id"])
    assert result is True

    active = await get_active_share_link(2)
    assert active is None

    by_token = await get_share_link_by_token(link["token"])
    assert by_token is None


@pytest.mark.asyncio
async def test_create_share_link_with_expiry():
    from backend.services.share_service import create_share_link, deactivate_share_link, get_active_share_link

    existing = await get_active_share_link(2)
    if existing:
        await deactivate_share_link(existing["id"])

    link = await create_share_link(2, expires_in_days=7)
    assert link["expires_at"] is not None

    await deactivate_share_link(link["id"])


@pytest.mark.asyncio
async def test_increment_view_count():
    from backend.services.share_service import (
        get_active_share_link,
        increment_view_count,
        get_share_link,
    )

    link = await get_active_share_link(1)
    assert link is not None
    initial_count = link["view_count"]

    await increment_view_count(link["id"])
    updated = await get_share_link(link["id"])
    assert updated["view_count"] == initial_count + 1


@pytest.mark.asyncio
async def test_get_shared_screenshot_data():
    from backend.services.share_service import get_active_share_link, get_shared_screenshot_data

    link = await get_active_share_link(1)
    data = await get_shared_screenshot_data(link["token"])
    assert data is not None
    assert "screenshot" in data
    assert "game" in data
    assert "share_link" in data
    assert data["screenshot"]["filename"] == "shot1.png"
    assert data["game"]["name"] == "Test Game"


@pytest.mark.asyncio
async def test_get_shared_data_invalid_token():
    from backend.services.share_service import get_shared_screenshot_data

    data = await get_shared_screenshot_data("totally-bogus-token")
    assert data is None


@pytest.mark.asyncio
async def test_create_share_link_nonexistent_screenshot():
    from backend.services.share_service import create_share_link

    with pytest.raises(ValueError, match="9999"):
        await create_share_link(9999)


@pytest.mark.asyncio
async def test_expired_link_auto_deactivates():
    from backend.services.share_service import get_share_link_by_token

    db = await db_mod.get_db()
    await db.execute(
        """INSERT INTO share_links (screenshot_id, token, expires_at, is_active)
           VALUES (1, 'expired-test-token', '2020-01-01T00:00:00', 1)"""
    )
    await db.commit()

    result = await get_share_link_by_token("expired-test-token")
    assert result is None

    cursor = await db.execute(
        "SELECT is_active FROM share_links WHERE token = 'expired-test-token'"
    )
    row = await cursor.fetchone()
    assert row[0] == 0


# ── Share Router Tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_share_router_create():
    from backend.routers.share import router
    from backend.services.share_service import get_active_share_link, deactivate_share_link

    existing = await get_active_share_link(2)
    if existing:
        await deactivate_share_link(existing["id"])

    result = await router.routes[0].endpoint(screenshot_id=2)
    assert result is not None
    assert "token" in result


@pytest.mark.asyncio
async def test_share_router_get():
    from backend.routers.share import router

    result = await router.routes[1].endpoint(screenshot_id=1)
    assert result is not None

    result_none = await router.routes[1].endpoint(screenshot_id=999)
    assert result_none is None


@pytest.mark.asyncio
async def test_share_router_delete():
    from fastapi import HTTPException
    from backend.routers.share import router
    from backend.services.share_service import create_share_link, get_active_share_link, deactivate_share_link

    existing = await get_active_share_link(2)
    if existing:
        await deactivate_share_link(existing["id"])
    await create_share_link(2)

    result = await router.routes[2].endpoint(screenshot_id=2)
    assert "message" in result

    with pytest.raises(HTTPException) as exc_info:
        await router.routes[2].endpoint(screenshot_id=2)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_share_public_page():
    from fastapi.responses import HTMLResponse
    from backend.routers.share import router
    from backend.services.share_service import get_active_share_link

    link = await get_active_share_link(1)
    assert link is not None

    response = await router.routes[3].endpoint(token=link["token"])
    assert isinstance(response, HTMLResponse)

    body = response.body.decode()
    assert "og:title" in body
    assert "og:image" in body
    assert "twitter:card" in body
    assert "GameVault" in body
    assert "Test Game" in body


@pytest.mark.asyncio
async def test_share_public_page_invalid_token():
    from fastapi.responses import HTMLResponse
    from backend.routers.share import router

    response = await router.routes[3].endpoint(token="invalid-token-xyz")
    assert isinstance(response, HTMLResponse)
    assert response.status_code == 404

    body = response.body.decode()
    assert "Not Found" in body


@pytest.mark.asyncio
async def test_share_image_redirect():
    from fastapi.responses import RedirectResponse
    from backend.routers.share import router
    from backend.services.share_service import get_active_share_link

    link = await get_active_share_link(1)
    response = await router.routes[4].endpoint(token=link["token"])
    assert isinstance(response, RedirectResponse)


@pytest.mark.asyncio
async def test_share_image_redirect_invalid():
    from fastapi import HTTPException
    from backend.routers.share import router

    with pytest.raises(HTTPException) as exc_info:
        await router.routes[4].endpoint(token="bad-token")
    assert exc_info.value.status_code == 404


# ── Settings Router Tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_settings_endpoint():
    from backend.routers.settings import get_settings

    result = await get_settings()
    assert "base_url" in result
    assert "library_dir" in result
    assert "has_steam_api_key" in result
    assert "has_steamgriddb_api_key" in result
    assert "has_igdb_credentials" in result
    assert "library_size" in result
    assert "game_count" in result
    assert "screenshot_count" in result
    assert "annotation_count" in result
    assert "active_share_count" in result
    assert "import_session_count" in result

    assert result["game_count"] == 1
    assert result["screenshot_count"] == 2
    assert result["annotation_count"] == 1


@pytest.mark.asyncio
async def test_settings_health():
    from backend.routers.settings import health_check

    result = await health_check()
    assert result["status"] == "ok"


# ── HTML Template Tests ──────────────────────────────────────────────────────

def test_render_share_page_html():
    from backend.routers.share import _render_share_page

    html = _render_share_page(
        title="Test Screenshot - Elden Ring",
        description="A beautiful vista",
        image_url="https://example.com/image.jpg",
        page_url="https://example.com/share/abc",
        screenshot={"id": 1, "filename": "test.png", "width": 1920, "height": 1080},
        game={"name": "Elden Ring"},
        annotation=None,
    )

    assert "<!DOCTYPE html>" in html
    assert "og:title" in html
    assert "og:description" in html
    assert "og:image" in html
    assert "Elden Ring" in html
    assert "1920 x 1080" in html
    assert "twitter:card" in html


def test_render_share_page_with_annotation():
    from backend.routers.share import _render_share_page

    html = _render_share_page(
        title="Test",
        description="Desc",
        image_url="https://example.com/img.jpg",
        page_url="https://example.com/share/xyz",
        screenshot={"id": 1, "filename": "test.png"},
        game={"name": "Dark Souls"},
        annotation={"content": "Boss fight!", "content_html": "<p>Boss fight!</p>"},
    )

    assert "Boss fight!" in html
    assert "annotation" in html


def test_render_error_page():
    from backend.routers.share import _render_error_page

    html = _render_error_page("Not Found", "This link has expired.")
    assert "Not Found" in html
    assert "expired" in html
    assert "<!DOCTYPE html>" in html


def test_html_escape():
    from backend.routers.share import _render_share_page

    html = _render_share_page(
        title='<script>alert("xss")</script>',
        description='Test "quotes" & <tags>',
        image_url="https://example.com/img.jpg",
        page_url="https://example.com/share/xyz",
        screenshot={"id": 1, "filename": "test.png"},
        game={"name": '<img onerror="alert(1)">'},
        annotation=None,
    )

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert 'onerror="alert' not in html


# ── Auth Password Change Tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_password_change_no_password_set():
    from fastapi import HTTPException
    from backend.routers.auth import ChangePasswordRequest, change_password

    req = ChangePasswordRequest(current_password="old", new_password="newpass123")
    with pytest.raises(HTTPException) as exc_info:
        await change_password(req)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_password_setup_and_change():
    from fastapi import HTTPException
    from backend.routers.auth import SetupRequest, ChangePasswordRequest, setup, change_password

    old_disable = settings.disable_auth
    settings.disable_auth = False

    try:
        result = await setup(SetupRequest(password="initial123"))
        assert "token" in result.model_dump()

        result2 = await change_password(
            ChangePasswordRequest(current_password="initial123", new_password="newpass456")
        )
        assert "message" in result2

        with pytest.raises(HTTPException) as exc_info:
            await change_password(
                ChangePasswordRequest(current_password="wrong", new_password="another789")
            )
        assert exc_info.value.status_code == 401
    finally:
        settings.disable_auth = old_disable
