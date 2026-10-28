"""Regression tests for the maintenance/security review, using disposable data."""

import asyncio

import httpx
import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request


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


async def test_spa_contains_encoded_paths_and_unknown_api(tmp_path):
    from backend.main import mount_frontend
    dist = tmp_path / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("SPA")
    (tmp_path / "outside.txt").write_text("private")
    app = FastAPI()
    mount_frontend(app, dist)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for path in ("/%2e%2e/%2e%2e/outside.txt", "/..%5c..%5coutside.txt", "/api/missing", "/share/missing"):
            response = await client.get(path)
            assert response.status_code == 404 or ("\\" in httpx.URL(path).path and response.text == "SPA")
            assert response.text != "private"
        assert (await client.get("/games/1")).text == "SPA"


async def test_password_change_revokes_old_and_legacy_tokens(library, monkeypatch):
    from backend import auth
    monkeypatch.setattr(library, "disable_auth", False)
    await auth.set_password("first-password")
    old = auth.create_access_token(password_hash=await auth.get_password_hash())

    def request(token):
        return Request({"type": "http", "path": "/api/games", "headers":
                        [(b"authorization", f"Bearer {token}".encode())], "query_string": b""})

    await auth.require_auth(request(old))
    await auth.set_password("second-password")
    for token in (old, auth.create_access_token()):
        with pytest.raises(HTTPException) as exc:
            await auth.require_auth(request(token))
        assert exc.value.status_code == 401
    fresh = auth.create_access_token(password_hash=await auth.get_password_hash())
    await auth.require_auth(request(fresh))


async def test_rollback_in_another_task_cannot_erase_annotation(library):
    from backend.database import get_db
    from backend.services import game_service, screenshot_service
    game = await game_service.create_game("Isolation")
    shot = await screenshot_service.create_screenshot(game["id"], "a.png", "a.png")
    ready, proceed = asyncio.Event(), asyncio.Event()

    async def save():
        db = await get_db()
        await db.execute("INSERT INTO annotations (screenshot_id, content) VALUES (?, 'kept')", (shot["id"],))
        ready.set()
        await proceed.wait()
        await db.commit()

    task = asyncio.create_task(save())
    await ready.wait()
    await (await get_db()).rollback()
    proceed.set()
    await task
    assert (await screenshot_service.get_annotation(shot["id"]))["content"] == "kept"


async def test_merge_retains_inflight_files_and_rolls_back_on_error(library, monkeypatch):
    from backend.database import get_db
    from backend.services import game_service as games
    from backend.services import screenshot_service as shots
    source, target = await games.create_game("Source"), await games.create_game("Target")
    folder = library.library_dir / source["folder_name"] / "screenshots"
    (folder / "a.png").write_bytes(b"original")
    (folder / "untracked.png").write_bytes(b"still uploading")
    shot = await shots.create_screenshot(source["id"], "a.png", f"{source['folder_name']}/screenshots/a.png")
    original_sync = shots._sync_fts

    async def fail(*args, **kwargs):
        raise RuntimeError("Simulated interruption")

    monkeypatch.setattr(shots, "_sync_fts", fail)
    with pytest.raises(RuntimeError):
        await games.merge_games(source["id"], target["id"])
    assert (await shots.get_screenshot(shot["id"]))["game_id"] == source["id"]
    assert (folder / "a.png").read_bytes() == b"original"
    monkeypatch.setattr(shots, "_sync_fts", original_sync)
    db = await get_db()
    await db.execute("INSERT INTO upload_jobs (task_id, game_id) VALUES ('pending', ?)", (source["id"],))
    await db.commit()
    await games.merge_games(source["id"], target["id"])
    late = await shots.create_screenshot(source["id"], "untracked.png", f"{source['folder_name']}/screenshots/untracked.png")
    assert late["game_id"] == target["id"]
    assert (folder / "untracked.png").read_bytes() == b"still uploading"
    assert (await (await db.execute("SELECT game_id FROM upload_jobs WHERE task_id = 'pending'")).fetchone())[0] == target["id"]
    assert (await games.get_game(target["id"]))["screenshot_count"] == 2


@pytest.mark.parametrize("detach_anchor", [True, False])
async def test_ungroup_preserves_notes_favorites_and_dismissal(library, detach_anchor):
    from backend.services import game_service as games
    from backend.services import screenshot_service as shots
    from backend.services.duplicate_service import list_candidates
    game = await games.create_game("Pair")
    first = await shots.create_screenshot(game["id"], "one.png", "one.png", is_hdr=False)
    second = await shots.create_screenshot(game["id"], "one.jxr", "one.jxr", is_hdr=True, capture_group_id=first["id"])
    await shots.save_annotation(first["id"], "Keep this note")
    await shots.toggle_favorite(first["id"])
    await shots.ungroup_screenshot(first["id"] if detach_anchor else second["id"])
    for shot in (first, second):
        assert (await shots.get_capture_details(shot["id"]))["variant_count"] == 1
        assert (await shots.get_annotation(shot["id"]))["content"] == "Keep this note"
        assert (await shots.get_screenshot(shot["id"]))["is_favorite"]
    assert (await games.get_game(game["id"]))["screenshot_count"] == 2
    assert (await list_candidates("dismissed"))[1] == 1


def test_rgb16_decode_retains_low_bits_and_cache_names_are_distinct(library, tmp_path):
    import imagecodecs
    from PIL import Image

    from backend.services import hdr_processor, image_processor
    rgb = np.array([[[257, 1025, 32769], [65535, 4097, 8193]]], dtype=np.uint16)
    hdr = tmp_path / "scene.png"
    sdr = tmp_path / "scene.jpg"
    hdr.write_bytes(imagecodecs.png_encode(rgb))
    Image.new("RGB", (2, 1), "red").save(sdr)
    assert hdr_processor.is_hdr_png(hdr)
    decoded = hdr_processor.decode_hdr_png(hdr)
    np.testing.assert_allclose(decoded, rgb.astype(np.float32) / 65535, atol=1e-7)
    assert image_processor.derivative_filename(hdr) != image_processor.derivative_filename(sdr)


async def test_storage_scans_are_cached_and_do_not_block_event_loop(library, monkeypatch):
    import threading

    from backend.services import storage_stats
    started, release = threading.Event(), threading.Event()
    calls = []

    def scan():
        calls.append(1)
        started.set()
        assert release.wait(5)
        return 123

    monkeypatch.setattr(storage_stats, "get_library_size_bytes", scan)
    monkeypatch.setattr(storage_stats, "get_thumbnail_cache_size_bytes", lambda: 456)
    task = asyncio.create_task(storage_stats.get_storage_sizes())
    assert await asyncio.to_thread(started.wait, 5)
    release.set()
    assert await task == (123, 456)
    assert await storage_stats.get_storage_sizes() == (123, 456)
    assert len(calls) == 1


async def test_upload_auth_happens_before_any_spooling(library, monkeypatch):
    import starlette.formparsers

    from backend import auth
    from backend.routers.upload import router
    monkeypatch.setattr(library, "disable_auth", False)
    await auth.set_password("test-password")
    def unexpected_spool(*args, **kwargs):
        pytest.fail("Unauthenticated body was spooled")
    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", unexpected_spool)
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/upload", data={"game_id": "1"}, files={"files": ("large.jxr", b"x" * (2 * 1024**2))})
    assert response.status_code == 401


async def test_upload_caps_each_file_not_the_whole_batch(library, monkeypatch):
    from backend.routers import upload
    from backend.services.game_service import create_game
    monkeypatch.setattr(library, "max_upload_size_mb", 1)
    monkeypatch.setattr(library, "max_upload_batch_size_mb", 4)
    game = await create_game("Uploads")
    app = FastAPI()
    app.include_router(upload.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/upload", data={"game_id": str(game["id"])},
            files=[("files", (name, b"x" * (768 * 1024))) for name in ("a.jxr", "b.png")])
        assert response.status_code == 200
        response = await client.post("/api/upload", data={"game_id": str(game["id"])},
            files={"files": ("large.jxr", b"x" * (1024**2 + 1))})
        assert response.status_code == 413
        assert "File exceeds" in response.text
        monkeypatch.setattr(library, "max_upload_size_mb", 512)
        monkeypatch.setattr(library, "max_upload_batch_size_mb", 8192)
        response = await client.post("/api/upload", data={"game_id": str(game["id"])},
            files={"files": ("detailed-hdr.jxr", b"x" * (8 * 1024**2))})
        assert response.status_code == 200


async def test_search_and_timeline_count_captures_and_match_any_variant(library):
    from backend.services import game_service as games
    from backend.services import screenshot_service as shots
    from backend.services.search_service import search_screenshots
    from backend.services.timeline_service import get_timeline, get_timeline_stats
    game = await games.create_game("Logical captures")
    first = await shots.create_screenshot(game["id"], "SDR.png", "SDR.png", is_hdr=False)
    second = await shots.create_screenshot(game["id"], "HDR-special.jxr", "HDR-special.jxr",
                                           is_hdr=True, capture_group_id=first["id"])
    await shots.save_annotation(second["id"], "A unique note")
    await shots.toggle_favorite(second["id"])
    for query in ("", "Logical", "special", "unique"):
        rows, total = await search_screenshots(query, favorites_only=True)
        assert total == 1
        assert rows[0]["screenshot_id"] == first["id"]
        assert rows[0]["has_annotation"]
    timeline = await get_timeline()
    assert len(timeline["days"]) == 1
    assert timeline["days"][0]["screenshot_count"] == 1
    assert timeline["days"][0]["screenshots"][0]["has_annotation"]
    assert (await get_timeline_stats())["total_screenshots"] == 1


async def test_import_finishing_during_merge_uses_redirect(library, monkeypatch):
    from backend.services import game_service as games
    from backend.services import screenshot_service as shots
    source, target = await games.create_game("Concurrent source"), await games.create_game("Concurrent target")
    await shots.create_screenshot(source["id"], "a.png", "a.png")
    reached, resume = asyncio.Event(), asyncio.Event()
    original = shots._sync_fts

    async def pause(*args, **kwargs):
        reached.set()
        await resume.wait()
        await original(*args, **kwargs)

    monkeypatch.setattr(shots, "_sync_fts", pause)
    async with asyncio.timeout(10):
        merging = asyncio.create_task(games.merge_games(source["id"], target["id"]))
        await reached.wait()
        importing = asyncio.create_task(shots.create_screenshot(source["id"], "late.png", "late.png"))
        await asyncio.sleep(0)
        resume.set()
        await merging
        assert (await importing)["game_id"] == target["id"]
        assert (await games.get_game(target["id"]))["screenshot_count"] == 2


async def test_cancelled_task_rolls_back_its_connection(library):
    from backend.database import get_db
    ready = asyncio.Event()

    async def abandoned():
        db = await get_db()
        await db.execute("INSERT INTO app_settings (key, value) VALUES ('cancelled', 'no')")
        ready.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(abandoned())
    await ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    db = await get_db()
    await db.execute("INSERT INTO app_settings (key, value) VALUES ('survivor', 'yes')")
    await db.commit()
    assert await (await db.execute("SELECT value FROM app_settings WHERE key = 'cancelled'")).fetchone() is None


async def test_chunked_upload_batch_cap_closes_spooled_files(library, monkeypatch):
    import starlette.formparsers

    from backend.routers.upload import router
    monkeypatch.setattr(library, "max_upload_size_mb", 4)
    monkeypatch.setattr(library, "max_upload_batch_size_mb", 1)
    spools = []
    original = starlette.formparsers.SpooledTemporaryFile
    def track(*args, **kwargs):
        spool = original(*args, **kwargs)
        spools.append(spool)
        return spool
    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", track)
    async def chunks():
        yield b'--boundary\r\nContent-Disposition: form-data; name="files"; filename="hdr.jxr"\r\n\r\n'
        for _ in range(20):
            yield b'x' * 65536
        yield b'\r\n--boundary--\r\n'
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/upload", content=chunks(), headers={"Content-Type": "multipart/form-data; boundary=boundary"})
    assert response.status_code == 413
    assert spools and all(spool.closed for spool in spools)
