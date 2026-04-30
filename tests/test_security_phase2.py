"""Tests for the Phase 2 security fixes from SECURITY_AUDIT_2026-04-27.

Covers:
  - GV-008: safe_library_path rejects traversal/absolute paths
  - GV-007: /share/{token}/image serves bytes directly; OG meta points there
  - GV-005: ?token= only honoured on the allowlisted paths
  - GV-002: PublicScreenshot/PublicGame drop internal fields
  - GV-009: security-headers middleware applies CSP + standard headers
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request


# ── GV-008: safe_library_path ────────────────────────────────────────────────


class TestSafeLibraryPath:
    def test_accepts_normal_relative_path(self, tmp_path: Path, monkeypatch):
        from backend.config import settings
        from backend.services.filesystem import safe_library_path

        monkeypatch.setattr(settings, "library_dir", tmp_path)
        sub = tmp_path / "Game" / "screenshots" / "shot.jpg"
        sub.parent.mkdir(parents=True)
        sub.write_text("x")

        out = safe_library_path("Game/screenshots/shot.jpg")
        assert out == sub.resolve()

    def test_rejects_parent_traversal(self, tmp_path: Path, monkeypatch):
        from backend.config import settings
        from backend.services.filesystem import safe_library_path

        monkeypatch.setattr(settings, "library_dir", tmp_path)

        with pytest.raises(HTTPException) as exc:
            safe_library_path("../etc/passwd")
        assert exc.value.status_code == 404

    def test_rejects_deep_traversal(self, tmp_path: Path, monkeypatch):
        from backend.config import settings
        from backend.services.filesystem import safe_library_path

        monkeypatch.setattr(settings, "library_dir", tmp_path)

        with pytest.raises(HTTPException) as exc:
            safe_library_path("Game/../../etc/passwd")
        assert exc.value.status_code == 404

    def test_rejects_absolute_path(self, tmp_path: Path, monkeypatch):
        from backend.config import settings
        from backend.services.filesystem import safe_library_path

        monkeypatch.setattr(settings, "library_dir", tmp_path)

        with pytest.raises(HTTPException) as exc:
            safe_library_path("/etc/passwd")
        assert exc.value.status_code == 404

    def test_rejects_empty_and_none(self, tmp_path: Path, monkeypatch):
        from backend.config import settings
        from backend.services.filesystem import safe_library_path

        monkeypatch.setattr(settings, "library_dir", tmp_path)

        for bad in ("", None):
            with pytest.raises(HTTPException) as exc:
                safe_library_path(bad)
            assert exc.value.status_code == 404


# ── GV-005: scoped ?token= acceptance ────────────────────────────────────────


def _build_request(path: str, query: str = "", auth_header: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if auth_header:
        headers.append((b"authorization", auth_header.encode()))
    return Request({
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": headers,
        "query_string": query.encode(),
        "client": ("1.2.3.4", 1234),
    })


class TestScopedQueryToken:
    @pytest.mark.parametrize("path", [
        "/api/upload/progress/abc-123",
        "/api/steam/import/42/progress",
        "/api/specialk/import/42/progress",
        "/api/screenshots/1/image",
        "/api/screenshots/1/thumb/sm",
        "/api/screenshots/1/thumb/md",
        "/api/games/7/cover",
    ])
    def test_query_token_allowed_on_whitelisted_paths(self, path):
        from backend.auth import _query_token_allowed
        assert _query_token_allowed(path) is True

    @pytest.mark.parametrize("path", [
        "/api/games",
        "/api/games/7",
        "/api/screenshots/1",
        "/api/screenshots/1/annotation",
        "/api/upload",
        "/api/upload/sync",
        "/api/settings",
        "/api/search",
        "/api/games/7/refresh-metadata",
    ])
    def test_query_token_rejected_on_other_paths(self, path):
        from backend.auth import _query_token_allowed
        assert _query_token_allowed(path) is False

    @pytest.mark.asyncio
    async def test_query_token_ignored_on_non_whitelisted_path(self, monkeypatch):
        """Even if a token is sent via ?token=, require_auth must NOT honour
        it on paths outside the allowlist — it returns 401 as if no token
        was supplied."""
        from backend import auth, config

        monkeypatch.setattr(config.settings, "disable_auth", False)

        async def _fake_get_password_hash():
            return "pretend-bcrypt"

        monkeypatch.setattr(auth, "get_password_hash", _fake_get_password_hash)

        # /api/games is NOT in the allowlist — query token must be ignored
        request = _build_request("/api/games", query="token=anything")
        with pytest.raises(HTTPException) as exc:
            await auth.require_auth(request)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_query_token_consulted_on_whitelisted_path(self, monkeypatch):
        """On an allowlisted path, ``?token=`` is read; an invalid value
        gets a 401 from JWT decoding rather than the 'missing token' 401."""
        from backend import auth, config

        monkeypatch.setattr(config.settings, "disable_auth", False)

        async def _fake_get_password_hash():
            return "pretend-bcrypt"

        monkeypatch.setattr(auth, "get_password_hash", _fake_get_password_hash)

        # /api/screenshots/1/image IS in the allowlist
        request = _build_request("/api/screenshots/1/image", query="token=garbage")
        with pytest.raises(HTTPException) as exc:
            await auth.require_auth(request)
        # The token was read and rejected (decode failure) — different
        # message than "missing"
        assert exc.value.status_code == 401
        assert "expired" in exc.value.detail.lower() or \
            "invalid" in exc.value.detail.lower()


# ── GV-002: slim public-gallery response shapes ──────────────────────────────


class TestPublicGalleryShapes:
    def test_public_screenshot_drops_sensitive_fields(self):
        from backend.models.screenshot import PublicScreenshot

        full = {
            "id": 1,
            "game_id": 2,
            "filename": "shot.png",
            "thumbnail_path_sm": "Game/thumbnails/300/shot.jpg",
            "thumbnail_path_md": "Game/thumbnails/800/shot.jpg",
            "width": 1920,
            "height": 1080,
            "taken_at": "2026-04-01T12:00:00",
            "uploaded_at": "2026-04-01T12:01:00",
            # Internal fields the public must NOT see
            "file_path": "Game/screenshots/shot.png",
            "sha256_hash": "abc123" * 10,
            "exif_data": '{"GPS": "...", "DeviceModel": "..."}',
            "steam_screenshot_id": "9999",
            "steam_description": "private notes",
            "source": "specialk_import",
            "is_favorite": True,
            "view_count": 100,
            "format": "png",
            "file_size": 999999,
            "created_at": "2026-04-01T12:01:00",
            "updated_at": "2026-04-02T12:01:00",
        }

        slim = PublicScreenshot.model_validate(full).model_dump()

        # Allowed fields present
        for k in ("id", "game_id", "filename", "thumbnail_path_sm",
                  "thumbnail_path_md", "width", "height", "taken_at",
                  "uploaded_at"):
            assert k in slim

        # Sensitive fields dropped
        for k in ("file_path", "sha256_hash", "exif_data",
                  "steam_screenshot_id", "steam_description", "source",
                  "is_favorite", "view_count", "format", "file_size",
                  "created_at", "updated_at"):
            assert k not in slim, f"PublicScreenshot leaked sensitive field {k!r}"

    def test_public_game_drops_sensitive_fields(self):
        from backend.models.game import PublicGame

        full = {
            "id": 1,
            "name": "My Game",
            "screenshot_count": 12,
            "developer": "Dev",
            "publisher": "Pub",
            "release_date": "2024-01-01",
            "genres": "Action",
            "description": "A game.",
            "first_screenshot_date": "2024-01-02",
            "last_screenshot_date": "2024-04-15",
            "steam_app_id": 730,
            # Internal fields
            "folder_name": "My Game",
            "cover_image_path": "My Game/cover.jpg",
            "header_image_url": "https://example/header.jpg",
            "igdb_id": 12345,
            "steamgriddb_id": 67890,
            "is_public": True,
            "created_at": "2024-01-01",
            "updated_at": "2024-04-15",
        }

        slim = PublicGame.model_validate(full).model_dump()

        # Allowed
        for k in ("id", "name", "screenshot_count", "steam_app_id",
                  "developer", "publisher", "release_date", "genres",
                  "description"):
            assert k in slim

        # Internal fields dropped
        for k in ("folder_name", "cover_image_path", "header_image_url",
                  "igdb_id", "steamgriddb_id", "is_public", "created_at",
                  "updated_at"):
            assert k not in slim, f"PublicGame leaked internal field {k!r}"


# ── GV-007: share template OG meta + image route ────────────────────────────


class TestShareImageFlow:
    def test_share_template_og_image_points_at_share_route(self):
        """The share page's OG image meta tag must point at the
        token-bound /share/{token}/image route, not the auth-protected
        /api/screenshots/{id}/image."""
        from backend.routers.share import _render_share_page

        html = _render_share_page(
            title="t",
            description="d",
            image_url="https://example.com/share/abc/image",
            page_url="https://example.com/share/abc",
            screenshot={"id": 5, "filename": "s.jpg", "width": 100, "height": 50},
            game={"name": "G"},
            annotation=None,
        )

        assert 'property="og:image"' in html
        assert "/share/abc/image" in html
        # Must NOT have an OG image pointing at the auth-protected route
        assert 'og:image" content="https://example.com/api/screenshots/' not in html

    @pytest.mark.asyncio
    async def test_share_image_404_for_unknown_token(self, monkeypatch):
        from backend.routers import share as share_router

        async def _no_data(_token):
            return None

        monkeypatch.setattr(share_router, "get_shared_screenshot_data", _no_data)

        with pytest.raises(HTTPException) as exc:
            await share_router.share_image("nope")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_share_image_serves_file_directly(
        self, tmp_path: Path, monkeypatch
    ):
        """The valid-token path returns a FileResponse, not a redirect to
        /api/screenshots/...; the caller does not need a JWT."""
        from fastapi.responses import FileResponse, RedirectResponse

        from backend.config import settings
        from backend.routers import share as share_router

        monkeypatch.setattr(settings, "library_dir", tmp_path)

        # Real on-disk file
        rel = "Game/screenshots/shot.png"
        (tmp_path / "Game" / "screenshots").mkdir(parents=True)
        (tmp_path / rel).write_bytes(b"\x89PNG fake")

        async def _data(_token):
            return {
                "screenshot": {"id": 1, "file_path": rel, "filename": "shot.png"},
                "game": {"name": "Game"},
                "annotation": None,
            }

        monkeypatch.setattr(share_router, "get_shared_screenshot_data", _data)

        result = await share_router.share_image("anytoken")
        assert isinstance(result, FileResponse)
        assert not isinstance(result, RedirectResponse)


# ── GV-009: security headers middleware ──────────────────────────────────────


class TestSecurityHeaders:
    def test_csp_strings_set_safe_defaults(self):
        from backend.main import _CSP_SHARE, _CSP_SPA

        # No 'unsafe-eval' (would re-enable eval-based XSS)
        assert "unsafe-eval" not in _CSP_SHARE
        assert "unsafe-eval" not in _CSP_SPA

        # No inline scripts
        assert "'unsafe-inline'" not in _CSP_SHARE.split("script-src")[1].split(";")[0]
        assert "'unsafe-inline'" not in _CSP_SPA.split("script-src")[1].split(";")[0]

        # Frame ancestors locked down
        assert "frame-ancestors 'none'" in _CSP_SHARE
        assert "frame-ancestors 'none'" in _CSP_SPA

        # Share template can't load remote scripts
        assert "script-src 'none'" in _CSP_SHARE

    @pytest.mark.asyncio
    async def test_middleware_adds_standard_headers(self):
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route
        from starlette.testclient import TestClient

        from backend.main import SecurityHeadersMiddleware

        async def hello(_request):
            return JSONResponse({"ok": True})

        app = Starlette(routes=[Route("/", hello)])
        app.add_middleware(SecurityHeadersMiddleware)

        with TestClient(app) as client:
            r = client.get("/")
            assert r.headers.get("X-Content-Type-Options") == "nosniff"
            assert r.headers.get("X-Frame-Options") == "DENY"
            assert r.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
            assert "Permissions-Policy" in r.headers
            csp = r.headers.get("Content-Security-Policy")
            assert csp is not None
            assert "frame-ancestors 'none'" in csp

    @pytest.mark.asyncio
    async def test_middleware_uses_share_csp_for_share_paths(self):
        from starlette.applications import Starlette
        from starlette.responses import HTMLResponse
        from starlette.routing import Route
        from starlette.testclient import TestClient

        from backend.main import SecurityHeadersMiddleware

        async def share_page(_request):
            return HTMLResponse("<p>share</p>")

        app = Starlette(routes=[Route("/share/{token}", share_page)])
        app.add_middleware(SecurityHeadersMiddleware)

        with TestClient(app) as client:
            r = client.get("/share/abc")
            csp = r.headers.get("Content-Security-Policy", "")
            # Share CSP forbids scripts entirely
            assert "script-src 'none'" in csp

    @pytest.mark.asyncio
    async def test_middleware_does_not_clobber_existing_csp(self):
        """Image responses set their own strict CSP; the middleware must
        not overwrite it."""
        from starlette.applications import Starlette
        from starlette.responses import Response
        from starlette.routing import Route
        from starlette.testclient import TestClient

        from backend.main import SecurityHeadersMiddleware

        async def img(_request):
            return Response(
                b"fake",
                media_type="image/jpeg",
                headers={"Content-Security-Policy": "default-src 'none'"},
            )

        app = Starlette(routes=[Route("/img", img)])
        app.add_middleware(SecurityHeadersMiddleware)

        with TestClient(app) as client:
            r = client.get("/img")
            assert r.headers.get("Content-Security-Policy") == "default-src 'none'"
