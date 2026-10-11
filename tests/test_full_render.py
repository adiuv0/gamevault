"""Full-resolution viewing for originals browsers can't display (JPEG XR, TIFF)."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

WIDTH, HEIGHT = 2560, 1440


@pytest.fixture
async def library(monkeypatch, tmp_path: Path):
    from backend.config import settings
    from backend.database import close_db, get_db, init_db

    await close_db()
    monkeypatch.setattr(settings, "db_path", tmp_path / "full.db")
    monkeypatch.setattr(settings, "library_dir", tmp_path / "library")
    monkeypatch.setattr(settings, "thumbnail_dir", tmp_path / "cache")
    monkeypatch.setattr(settings, "preview_max_width", 1920)
    await init_db()
    try:
        yield await get_db(), settings
    finally:
        await close_db()


def _write_jxr(path: Path) -> None:
    import imagecodecs

    # A smooth scRGB gradient with highlights above 1.0, like a real HDR capture.
    ramp = np.linspace(0.0, 2.0, WIDTH, dtype=np.float32)
    rgb = np.broadcast_to(ramp, (HEIGHT, WIDTH))
    pixels = np.stack([rgb, rgb * 0.5, rgb * 0.25, np.ones_like(rgb)], axis=-1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(imagecodecs.jpegxr_encode(pixels.astype(np.float16), level=80))


def _render_path(settings, screenshot: dict) -> Path:
    return settings.thumbnail_dir / "HDR Game" / "full" / Path(screenshot["preview_path"]).name


async def _create_jxr(db, settings, *, public: bool = False) -> dict:
    from backend.services.image_processor import generate_thumbnails_with_hash
    from backend.services.screenshot_service import create_screenshot

    cursor = await db.execute(
        "INSERT INTO games (name, folder_name, is_public) VALUES ('HDR Game', 'HDR Game', ?)",
        (1 if public else 0,),
    )
    await db.commit()
    source = settings.library_dir / "HDR Game" / "screenshots" / "capture.jxr"
    _write_jxr(source)
    sm, md, preview, _ = generate_thumbnails_with_hash(source, "HDR Game", "capture")
    return await create_screenshot(
        game_id=cursor.lastrowid,
        filename=source.name,
        file_path="HDR Game/screenshots/capture.jxr",
        width=WIDTH,
        height=HEIGHT,
        format="jxr",
        thumbnail_path_sm=sm,
        thumbnail_path_md=md,
        preview_path=preview,
    )


@pytest.mark.asyncio
async def test_jxr_full_view_is_a_full_resolution_jpeg(library):
    from backend.routers.screenshots import get_full, get_preview

    db, settings = library
    screenshot = await _create_jxr(db, settings)

    preview = await get_preview(screenshot["id"])
    with Image.open(preview.path) as image:
        assert image.width == 1920  # the bounded preview the viewer used to stop at

    response = await get_full(screenshot["id"])
    assert response.media_type == "image/jpeg"
    render = Path(response.path)
    assert render == _render_path(settings, screenshot)
    with Image.open(render) as image:
        assert image.format == "JPEG"
        assert image.size == (WIDTH, HEIGHT)


@pytest.mark.asyncio
async def test_import_renders_jxr_ahead_of_first_view(library, monkeypatch):
    from backend.routers.screenshots import get_full
    from backend.services import full_render_service

    db, settings = library
    screenshot = await _create_jxr(db, settings)
    render = _render_path(settings, screenshot)
    assert render.exists()
    with Image.open(render) as image:
        assert image.size == (WIDTH, HEIGHT)

    def fail(*_):
        raise AssertionError("render should already exist")

    monkeypatch.setattr(full_render_service, "generate_full_render", fail)
    assert Path((await get_full(screenshot["id"])).path) == render


@pytest.mark.asyncio
async def test_sdr_import_makes_no_full_render(library):
    from backend.services.image_processor import generate_thumbnails_with_hash

    _, settings = library
    source = settings.library_dir / "SDR" / "screenshots" / "shot.png"
    source.parent.mkdir(parents=True)
    Image.new("RGB", (WIDTH, HEIGHT), "teal").save(source)

    generate_thumbnails_with_hash(source, "SDR", "shot")

    assert not (settings.thumbnail_dir / "SDR" / "full").exists()


@pytest.mark.asyncio
async def test_cache_repair_renders_existing_jxr_captures(library):
    from backend.services import preview_service

    db, settings = library
    screenshot = await _create_jxr(db, settings)
    render = _render_path(settings, screenshot)
    render.unlink()  # a capture imported before renders were made at import

    job = await preview_service.start_backfill_job()
    await preview_service.run_backfill_job(job["id"])
    finished = await preview_service.get_latest_backfill_job()

    assert finished["status"] == "completed"
    assert finished["errors"] == 0
    with Image.open(render) as image:
        assert image.size == (WIDTH, HEIGHT)


@pytest.mark.asyncio
async def test_full_render_is_cached_after_first_view(library, monkeypatch):
    from backend.routers.screenshots import get_full
    from backend.services import full_render_service

    db, settings = library
    screenshot = await _create_jxr(db, settings)
    _render_path(settings, screenshot).unlink()  # exercise the on-demand path
    calls = []
    real = full_render_service.generate_full_render

    def counting(source, dest):
        calls.append(source)
        return real(source, dest)

    monkeypatch.setattr(full_render_service, "generate_full_render", counting)
    first = await get_full(screenshot["id"])
    second = await get_full(screenshot["id"])

    assert first.path == second.path
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_concurrent_first_views_render_once(library, monkeypatch):
    import asyncio

    from backend.routers.screenshots import get_full
    from backend.services import full_render_service

    db, settings = library
    screenshot = await _create_jxr(db, settings)
    _render_path(settings, screenshot).unlink()  # exercise the on-demand path
    calls = []
    real = full_render_service.generate_full_render

    def counting(source, dest):
        calls.append(source)
        return real(source, dest)

    monkeypatch.setattr(full_render_service, "generate_full_render", counting)
    responses = await asyncio.gather(*(get_full(screenshot["id"]) for _ in range(4)))

    assert {response.path for response in responses} == {responses[0].path}
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_browser_native_original_is_served_unchanged(library):
    from backend.routers.screenshots import get_full
    from backend.services.screenshot_service import create_screenshot

    db, settings = library
    cursor = await db.execute("INSERT INTO games (name, folder_name) VALUES ('SDR', 'SDR')")
    await db.commit()
    source = settings.library_dir / "SDR" / "screenshots" / "shot.png"
    source.parent.mkdir(parents=True)
    Image.new("RGB", (WIDTH, HEIGHT), "teal").save(source)
    screenshot = await create_screenshot(
        game_id=cursor.lastrowid,
        filename="shot.png",
        file_path="SDR/screenshots/shot.png",
        format="png",
    )

    response = await get_full(screenshot["id"])

    assert Path(response.path) == source.resolve()
    assert response.media_type == "image/png"
    assert not (settings.thumbnail_dir / "SDR" / "full").exists()


@pytest.mark.asyncio
async def test_undecodable_jxr_falls_back_to_the_preview(library):
    from backend.routers.screenshots import get_full

    db, settings = library
    screenshot = await _create_jxr(db, settings)
    source = settings.library_dir / "HDR Game" / "screenshots" / "capture.jxr"
    source.write_bytes(b"\x49\x49\xbc" + b"\x00" * 64)  # JXR magic, no image
    _render_path(settings, screenshot).unlink()

    response = await get_full(screenshot["id"])

    assert response.media_type == "image/jpeg"
    with Image.open(response.path) as image:
        assert image.width == 1920


@pytest.mark.asyncio
async def test_deleting_a_capture_removes_its_full_render(library):
    from backend.routers.screenshots import delete_screenshot, get_full

    db, settings = library
    screenshot = await _create_jxr(db, settings)
    render = Path((await get_full(screenshot["id"])).path)
    assert render.exists()

    await delete_screenshot(screenshot["id"])

    assert not render.exists()


@pytest.mark.asyncio
async def test_public_gallery_serves_full_render(library):
    from backend.routers.gallery import gallery_screenshot_full

    db, settings = library
    screenshot = await _create_jxr(db, settings, public=True)

    response = await gallery_screenshot_full(screenshot["id"])

    assert response.media_type == "image/jpeg"
    assert response.headers["cache-control"] == "public, max-age=86400"
    with Image.open(response.path) as image:
        assert image.size == (WIDTH, HEIGHT)


@pytest.mark.asyncio
async def test_share_link_image_is_a_browser_jpeg_for_jxr(library):
    from backend.routers.share import share_image
    from backend.services.share_service import create_share_link

    db, settings = library
    screenshot = await _create_jxr(db, settings)
    link = await create_share_link(screenshot["id"])

    response = await share_image(link["token"])

    assert response.media_type == "image/jpeg"
    with Image.open(response.path) as image:
        assert image.size == (WIDTH, HEIGHT)
