"""Tests for the Phase 1 security fixes from SECURITY_AUDIT_2026-04-27.

Covers:
  - GV-001: annotation render + sanitize pipeline blocks XSS, preserves formatting
  - GV-003: upload display-name sanitization strips path components
  - GV-004: streaming upload aborts at byte limit without leaving temp files
  - GV-006: rate limiter locks after threshold; change-password requires auth
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException


# ── GV-001: annotation sanitizer ─────────────────────────────────────────────


_DANGEROUS_TAG_NAMES = {
    "script", "img", "svg", "iframe", "embed", "object",
    "link", "style", "form", "input", "meta",
}


def _check_safe(html: str) -> None:
    """Parse ``html`` as the browser would and assert it contains no
    dangerous live elements, on-event attributes, or javascript:/data:
    URIs in href/src attributes. Escaped text content (``&lt;script&gt;``)
    is harmless and not flagged.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    # No dangerous tags as live elements
    for tag_name in _DANGEROUS_TAG_NAMES:
        found = soup.find(tag_name)
        assert found is None, f"dangerous <{tag_name}> element present: {found}"

    # No on-event attributes anywhere
    for tag in soup.find_all(True):
        bad = [a for a in tag.attrs if a.lower().startswith("on")]
        assert not bad, f"event-handler attrs on <{tag.name}>: {bad}"

    # href/src must be a safe scheme
    for tag in soup.find_all(["a", "area"]):
        href = tag.get("href")
        if not href:
            continue
        lowered = href.lower().strip()
        assert not lowered.startswith("javascript:"), \
            f"javascript: href present: {href}"
        assert not lowered.startswith("data:"), \
            f"data: href present: {href}"


class TestAnnotationSanitizer:
    def test_strips_script_tag(self):
        from backend.services.annotation_renderer import render_and_sanitize
        _check_safe(render_and_sanitize("<script>alert(1)</script>"))

    def test_strips_img_onerror(self):
        from backend.services.annotation_renderer import render_and_sanitize
        _check_safe(render_and_sanitize("<img src=x onerror=alert(1)>"))

    def test_strips_svg_onload(self):
        from backend.services.annotation_renderer import render_and_sanitize
        _check_safe(render_and_sanitize("<svg onload=alert(1)></svg>"))

    def test_strips_iframe(self):
        from backend.services.annotation_renderer import render_and_sanitize
        _check_safe(render_and_sanitize("<iframe src=javascript:alert(1)></iframe>"))

    def test_strips_javascript_link_from_markdown(self):
        from backend.services.annotation_renderer import render_and_sanitize
        _check_safe(render_and_sanitize("[click](javascript:alert(1))"))

    def test_strips_data_uri_html_link(self):
        from backend.services.annotation_renderer import render_and_sanitize
        _check_safe(render_and_sanitize("[click](data:text/html,<script>alert(1)</script>)"))

    def test_strips_inline_a_href_javascript(self):
        from backend.services.annotation_renderer import render_and_sanitize
        _check_safe(render_and_sanitize('<a href="javascript:alert(1)">x</a>'))

    def test_strips_object_with_data_uri(self):
        from backend.services.annotation_renderer import render_and_sanitize
        _check_safe(render_and_sanitize(
            '<object data="data:text/html,<script>1</script>"></object>'
        ))

    def test_strips_form_action(self):
        from backend.services.annotation_renderer import render_and_sanitize
        _check_safe(render_and_sanitize(
            '<form action="x"><input type=submit></form>'
        ))

    def test_preserves_bold_italic(self):
        from backend.services.annotation_renderer import render_and_sanitize
        out = render_and_sanitize("**bold** and *italic*")
        assert "<strong>bold</strong>" in out
        assert "<em>italic</em>" in out

    def test_preserves_https_link_with_rel(self):
        from backend.services.annotation_renderer import render_and_sanitize
        out = render_and_sanitize("[ex](https://example.com)")
        assert 'href="https://example.com"' in out
        assert 'rel="noopener noreferrer nofollow"' in out

    def test_preserves_mailto(self):
        from backend.services.annotation_renderer import render_and_sanitize
        out = render_and_sanitize("[me](mailto:me@example.com)")
        assert 'href="mailto:me@example.com"' in out

    def test_preserves_lists(self):
        from backend.services.annotation_renderer import render_and_sanitize
        out = render_and_sanitize("- a\n- b")
        assert "<ul>" in out
        assert "<li>a</li>" in out
        assert "<li>b</li>" in out

    def test_preserves_code_block(self):
        from backend.services.annotation_renderer import render_and_sanitize
        out = render_and_sanitize("```\nhello\n```")
        assert "<pre>" in out
        assert "<code>" in out
        assert "hello" in out

    def test_empty_input_safe(self):
        from backend.services.annotation_renderer import render_and_sanitize
        assert render_and_sanitize("") == ""
        assert render_and_sanitize(None) == ""  # type: ignore[arg-type]


# ── GV-003: upload display-name sanitization ─────────────────────────────────


class TestSafeDisplayName:
    def test_strips_unix_path(self):
        from backend.routers.upload import _safe_display_name
        assert _safe_display_name("../../etc/passwd") == "passwd"
        assert _safe_display_name("foo/bar/baz.png") == "baz.png"

    def test_strips_windows_path(self):
        from backend.routers.upload import _safe_display_name
        # Path.name uses the OS separator — on Linux runners we still want
        # backslashes treated as part of the name. The regex strip removes
        # them. Either way, the result must not contain a path separator.
        result = _safe_display_name("C:\\Windows\\System32\\evil.png")
        assert "\\" not in result
        assert "/" not in result
        assert ":" not in result

    def test_strips_control_chars(self):
        from backend.routers.upload import _safe_display_name
        out = _safe_display_name("hello\nworld\t<script>")
        assert "\n" not in out
        assert "\t" not in out
        assert "<" not in out
        assert ">" not in out

    def test_handles_none(self):
        from backend.routers.upload import _safe_display_name
        assert _safe_display_name(None) == "unnamed.jpg"

    def test_handles_empty(self):
        from backend.routers.upload import _safe_display_name
        assert _safe_display_name("") == "unnamed.jpg"
        # All-stripped → fall back
        assert _safe_display_name("///") == "unnamed.jpg"

    def test_caps_length(self):
        from backend.routers.upload import _safe_display_name
        out = _safe_display_name("a" * 500 + ".png")
        assert len(out) <= 120

    def test_preserves_safe_chars(self):
        from backend.routers.upload import _safe_display_name
        assert _safe_display_name("My Game (HDR) 2025.jxr") == "My Game (HDR) 2025.jxr"


# ── GV-004: streaming upload ─────────────────────────────────────────────────


class TestStreamingUpload:
    @pytest.mark.asyncio
    async def test_oversize_aborts_with_413(self, tmp_path: Path):
        from starlette.datastructures import UploadFile
        from backend.routers.upload import _save_upload_streaming

        big = UploadFile(
            filename="big.png",
            file=BytesIO(b"x" * (2 * 1024 * 1024)),  # 2 MB
        )
        with pytest.raises(HTTPException) as exc_info:
            await _save_upload_streaming(big, tmp_path, max_bytes=100 * 1024)  # 100 KB cap
        assert exc_info.value.status_code == 413

    @pytest.mark.asyncio
    async def test_oversize_leaves_no_temp_files(self, tmp_path: Path):
        from starlette.datastructures import UploadFile
        from backend.routers.upload import _save_upload_streaming

        big = UploadFile(
            filename="big.png",
            file=BytesIO(b"x" * (2 * 1024 * 1024)),
        )
        try:
            await _save_upload_streaming(big, tmp_path, max_bytes=100 * 1024)
        except HTTPException:
            pass

        assert list(tmp_path.iterdir()) == [], "leftover temp file after oversize abort"

    @pytest.mark.asyncio
    async def test_writes_to_uuid_named_file(self, tmp_path: Path):
        """The on-disk path is uuid-based — the user-supplied filename never
        becomes part of the path. This is the GV-003 traversal fix."""
        from starlette.datastructures import UploadFile
        from backend.routers.upload import _save_upload_streaming

        # User supplies an evil filename trying to escape tmp_path
        evil = UploadFile(
            filename="../../../etc/passwd",
            file=BytesIO(b"data"),
        )
        display, path = await _save_upload_streaming(evil, tmp_path, max_bytes=1024)

        # Path is INSIDE tmp_path
        assert path.parent == tmp_path
        # Filename is uuid hex + .bin (32 hex chars + extension)
        assert path.suffix == ".bin"
        assert len(path.stem) == 32
        assert all(c in "0123456789abcdef" for c in path.stem)
        # Display name is sanitized (no path components)
        assert "/" not in display
        assert ".." not in display

    @pytest.mark.asyncio
    async def test_under_limit_writes_full_content(self, tmp_path: Path):
        from starlette.datastructures import UploadFile
        from backend.routers.upload import _save_upload_streaming

        payload = b"hello world" * 100  # ~1.1 KB
        f = UploadFile(
            filename="small.png",
            file=BytesIO(payload),
        )
        display, path = await _save_upload_streaming(f, tmp_path, max_bytes=10 * 1024)
        assert path.read_bytes() == payload
        assert display == "small.png"


# ── GV-004: Pillow decompression bomb guard ──────────────────────────────────


class TestDecompressionBombGuard:
    def test_pillow_max_pixels_set_explicitly(self):
        """Importing image_processor must set Pillow's MAX_IMAGE_PIXELS to
        an explicit value rather than relying on the library default. This
        is checked at import time."""
        from PIL import Image

        import backend.services.image_processor  # noqa: F401

        assert Image.MAX_IMAGE_PIXELS == 144_000_000


# ── GV-006: rate limiter helper ──────────────────────────────────────────────


class TestRateLimiter:
    def test_locks_after_threshold(self):
        from backend.routers.auth import (
            _RATE_LIMIT_MAX,
            _check_rate_limit,
            _record_failed_attempt,
        )

        bucket: dict[str, list[float]] = {}
        ip = "1.2.3.4"

        # Up to threshold-1 failures: still passes
        for _ in range(_RATE_LIMIT_MAX):
            _check_rate_limit(bucket, ip, "test")
            _record_failed_attempt(bucket, ip)

        # The (_RATE_LIMIT_MAX + 1)-th check raises 429
        with pytest.raises(HTTPException) as exc_info:
            _check_rate_limit(bucket, ip, "test")
        assert exc_info.value.status_code == 429
        assert "test" in exc_info.value.detail

    def test_separate_buckets_isolate_login_and_change_password(self):
        """Login and change-password use distinct buckets so a logged-in
        attacker can't bypass login rate limiting via /change-password."""
        from backend.routers.auth import (
            _change_password_attempts,
            _login_attempts,
        )

        assert _login_attempts is not _change_password_attempts

    def test_clear_attempts_resets_bucket(self):
        from backend.routers.auth import _clear_attempts, _record_failed_attempt

        bucket: dict[str, list[float]] = {}
        ip = "1.2.3.4"
        for _ in range(3):
            _record_failed_attempt(bucket, ip)
        assert len(bucket[ip]) == 3
        _clear_attempts(bucket, ip)
        assert ip not in bucket


# ── GV-006: require_auth no longer auto-skips /api/auth/* ────────────────────


class TestAuthPathPrefix:
    """Behavioral test that require_auth no longer rubber-stamps /api/auth/*.

    The previous version of require_auth had a path-prefix skip list that
    included ``/api/auth/`` — which meant ``Depends(require_auth)`` on
    /api/auth/change-password silently became a no-op. The skip list now
    excludes /api/auth/*, so change-password's auth dependency actually
    fires.
    """

    @pytest.mark.asyncio
    async def test_change_password_path_no_longer_auto_skipped(self, monkeypatch):
        from starlette.requests import Request

        from backend import auth, config

        # Pretend auth is enabled and a password has been set
        monkeypatch.setattr(config.settings, "disable_auth", False)

        async def _fake_get_password_hash():
            return "pretend-bcrypt-hash"

        monkeypatch.setattr(auth, "get_password_hash", _fake_get_password_hash)

        # Build a fake request for /api/auth/change-password with no token
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/change-password",
            "headers": [],
            "query_string": b"",
            "client": ("1.2.3.4", 1234),
        }
        request = Request(scope)

        # With no token, require_auth should now raise 401 — proving the
        # /api/auth/ skip is gone.
        with pytest.raises(HTTPException) as exc_info:
            await auth.require_auth(request)
        assert exc_info.value.status_code == 401
