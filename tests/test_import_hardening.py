"""Steam download safety, retries, and single-pass image analysis."""

from io import BytesIO
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, "PNG")
    return buffer.getvalue()


def _scraper(handler):
    from backend.services.steam_scraper import SteamScraper

    scraper = SteamScraper("76561198000000000", is_numeric_id=True)
    scraper.rate_limit_ms = 0
    scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return scraper


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    from backend.services import steam_scraper

    monkeypatch.setattr(steam_scraper, "_retry_delay", lambda resp, attempt: 0)


async def test_redirect_to_disallowed_host_is_blocked():
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.host)
        if request.url.host.endswith("steamusercontent.com"):
            return httpx.Response(302, headers={"location": "https://internal.example/secret"})
        return httpx.Response(200, content=_png_bytes())

    scraper = _scraper(handler)
    try:
        assert await scraper.download_image("https://images.steamusercontent.com/a.png") is None
    finally:
        await scraper._client.aclose()
    assert requested == ["images.steamusercontent.com"]


async def test_download_rejects_non_images_and_retries_rate_limits():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if request.url.path == "/html":
            return httpx.Response(200, content=b"<html>" + b"x" * 5000)
        if calls["n"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, content=_png_bytes())

    scraper = _scraper(handler)
    try:
        assert await scraper.download_image("https://cdn.steamstatic.com/html") is None
        calls["n"] = 0
        assert await scraper.download_image("https://cdn.steamstatic.com/a.png") == _png_bytes()
        assert calls["n"] == 2
    finally:
        await scraper._client.aclose()


async def test_failing_later_grid_page_fails_instead_of_truncating():
    from backend.services.steam_scraper import SteamFetchError

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("p") == "1":
            html = '<a class="profile_media_item" href="https://steamcommunity.com/sharedfiles/filedetails/?id=1" data-publishedfileid="1"></a>'
            return httpx.Response(200, text=html)
        return httpx.Response(503)

    scraper = _scraper(handler)
    try:
        with pytest.raises(SteamFetchError):
            await scraper._scrape_game_screenshots_html(440)
    finally:
        await scraper._client.aclose()


def test_jxr_dimensions_and_describe_avoid_full_decode(tmp_path: Path, monkeypatch):
    import imagecodecs

    from backend.services import hdr_processor, image_processor

    jxr = tmp_path / "capture.jxr"
    jxr.write_bytes(imagecodecs.jpegxr_encode(np.zeros((36, 64, 3), dtype=np.float16)))

    def no_decode(_path):
        raise AssertionError("dimensions should come from the container header")

    monkeypatch.setattr(hdr_processor, "decode_jxr", no_decode)
    info = image_processor.describe_image(jxr)
    assert (info["width"], info["height"], info["format"], info["is_hdr"]) == (64, 36, "jxr", True)


def test_sha256_cache_returns_same_digest_and_tracks_changes(tmp_path: Path):
    from backend.services.image_processor import compute_sha256

    path = tmp_path / "a.bin"
    path.write_bytes(b"first")
    first = compute_sha256(path)
    assert compute_sha256(path) == first
    path.write_bytes(b"second, longer")
    assert compute_sha256(path) != first


def test_exclusive_copy_never_overwrites(tmp_path: Path):
    from backend.services.upload_service import _copy_exclusive

    source = tmp_path / "source.png"
    source.write_bytes(_png_bytes())
    target = tmp_path / "target.png"
    target.write_bytes(b"existing original")
    with pytest.raises(FileExistsError):
        _copy_exclusive(source, target)
    assert target.read_bytes() == b"existing original"


def test_jpeg_thumbnails_decode_at_reduced_scale(tmp_path: Path):
    from backend.services.image_processor import _open_for_thumbnail

    source = tmp_path / "steam.jpg"
    Image.new("RGB", (3840, 2160), "teal").save(source, quality=90)
    image = _open_for_thumbnail(source, 1920)
    try:
        assert image.size == (1920, 1080)
    finally:
        image.close()
    full = _open_for_thumbnail(source)
    try:
        assert full.size == (3840, 2160)
    finally:
        full.close()


def test_exclusive_copy_links_or_copies_into_new_name(tmp_path: Path):
    from backend.services.upload_service import _copy_exclusive

    source = tmp_path / "source.png"
    source.write_bytes(_png_bytes())
    target = tmp_path / "library" / "target.png"
    target.parent.mkdir()
    _copy_exclusive(source, target)
    source.unlink()  # staging cleanup must not take the library file with it
    assert target.read_bytes() == _png_bytes()
