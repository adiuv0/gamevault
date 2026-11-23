"""Regressions for the 1.2.2 fixes (bugs from the November 2026 external review)."""

import asyncio
from pathlib import Path

import httpx
import numpy as np
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


def _jxr_bytes(width: int = 64, height: int = 36) -> bytes:
    import imagecodecs

    ramp = np.linspace(-0.5, 2.0, width, dtype=np.float32)
    rgb = np.broadcast_to(ramp, (height, width))
    pixels = np.stack([rgb, rgb * 0.5, rgb * 0.25, np.ones_like(rgb)], axis=-1)
    return imagecodecs.jpegxr_encode(pixels.astype(np.float16), level=80)


# ── Public gallery ──────────────────────────────────────────────────────────


async def test_public_gallery_exposes_a_cover_version_and_serves_the_cover(library):
    """The gallery hid every cover: it checked cover_image_path, which the public API drops."""
    from backend.services import game_service

    with_cover = await game_service.create_game("Covered")
    without = await game_service.create_game("Bare")
    cover = library.library_dir / with_cover["folder_name"] / "cover.png"
    cover.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (6, 9), "blue").save(cover)
    await game_service.update_game(with_cover["id"], cover_image_path=f"{with_cover['folder_name']}/cover.png")

    async with _client() as client:
        games = {g["name"]: g for g in (await client.get("/api/gallery/games")).json()["games"]}
        assert games["Covered"]["cover_version"]
        assert games["Bare"]["cover_version"] is None
        assert "cover_image_path" not in games["Covered"]
        detail = (await client.get(f"/api/gallery/games/{with_cover['id']}")).json()
        assert detail["cover_version"] == games["Covered"]["cover_version"]

        response = await client.get(
            f"/api/gallery/games/{with_cover['id']}/cover?v={detail['cover_version']}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert (await client.get(f"/api/gallery/games/{without['id']}/cover")).status_code == 404


async def test_public_screenshot_has_no_thumbnail_paths(library):
    from backend.services import game_service, screenshot_service

    game = await game_service.create_game("Public")
    path = library.library_dir / game["folder_name"] / "screenshots" / "a.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), "red").save(path)
    shot = await screenshot_service.create_screenshot(
        game["id"], "a.png", f"{game['folder_name']}/screenshots/a.png",
        thumbnail_path_sm=f"{game['folder_name']}/thumbnails/300/v2-{'ab' * 32}.jpg",
        sha256_hash="ab" * 32,
    )
    async with _client() as client:
        body = (await client.get(f"/api/gallery/screenshots/{shot['id']}")).text
    assert "thumbnail_path" not in body
    assert "ab" * 32 not in body


# ── Game merge ──────────────────────────────────────────────────────────────


async def test_merge_keeps_the_steam_app_id(library):
    """Without it the next Steam import recreated the game that was merged away."""
    from backend.services import game_service

    steam_game = await game_service.create_game("Clair Obscur", steam_app_id=1903340)
    target = await game_service.create_game("Expedition 33")
    await game_service.merge_games(steam_game["id"], target["id"])

    merged = await game_service.get_game(target["id"])
    assert merged["steam_app_id"] == 1903340
    again = await game_service.get_or_create_game("Clair Obscur", steam_app_id=1903340)
    assert again["id"] == target["id"]


async def test_merge_keeps_the_targets_own_steam_app_id(library):
    from backend.services import game_service

    source = await game_service.create_game("A", steam_app_id=1)
    target = await game_service.create_game("B", steam_app_id=2)
    await game_service.merge_games(source["id"], target["id"])
    assert (await game_service.get_game(target["id"]))["steam_app_id"] == 2


# ── HDR ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("algorithm", ["reinhard", "aces", "clip"])
def test_tone_mapping_maps_negative_and_nan_scrgb_to_black(algorithm):
    """Wide-gamut scRGB has negative channels; Reinhard turned x < -1 into values > 1."""
    from backend.services.hdr_processor import tone_map

    rgb = np.array([[[-2.0, -5.0, -1.0], [np.nan, 0.5, 4.0]]], dtype=np.float32)
    out = tone_map(rgb, algorithm=algorithm)
    assert np.isfinite(out).all()
    assert out.min() >= 0.0 and out.max() <= 1.0
    assert (out[0, 0] == 0.0).all()
    assert out[0, 1, 0] == 0.0
    assert rgb[0, 0, 0] == -2.0  # the input array is left alone


def test_jxr_validation_reads_only_the_header(tmp_path, monkeypatch):
    """Import decoded every JXR twice: once to validate, once for thumbnails."""
    from backend.services import hdr_processor, image_processor

    source = tmp_path / "shot.jxr"
    source.write_bytes(_jxr_bytes())

    def no_decode(_path):
        raise AssertionError("validate_image decoded the whole file")

    monkeypatch.setattr(hdr_processor, "decode_jxr", no_decode)
    assert image_processor.validate_image(source) is True


def test_jxr_decode_respects_the_pixel_cap(tmp_path, monkeypatch):
    from backend.services import hdr_processor, image_processor

    source = tmp_path / "shot.jxr"
    source.write_bytes(_jxr_bytes(64, 36))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 64 * 36 - 1)
    with pytest.raises(ValueError, match="pixel limit"):
        hdr_processor.decode_jxr(source)
    assert image_processor.validate_image(source) is False


async def test_upload_rejects_a_jxr_whose_body_does_not_decode(library):
    """With header-only validation, a broken body must still fail and leave no file behind."""
    from backend.services import game_service
    from backend.services.upload_service import process_upload

    game = await game_service.create_game("Broken HDR")
    data = _jxr_bytes(256, 144)
    staged = library.data_dir / "staging" / "broken.jxr"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(data[: len(data) // 3])  # header intact, image data cut off

    results = await process_upload("t-broken", game["id"], [(0, "broken.jxr", staged)])

    assert results == []
    screenshots_dir = library.library_dir / game["folder_name"] / "screenshots"
    assert not any(screenshots_dir.glob("*")) if screenshots_dir.exists() else True


async def test_upload_still_accepts_a_good_jxr(library):
    from backend.services import game_service
    from backend.services.upload_service import process_upload

    game = await game_service.create_game("Good HDR")
    staged = library.data_dir / "staging" / "good.jxr"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(_jxr_bytes(256, 144))

    results = await process_upload("t-good", game["id"], [(0, "good.jxr", staged)])

    assert len(results) == 1
    assert results[0]["thumbnail_path_sm"]


# ── Import concurrency ──────────────────────────────────────────────────────


async def test_identical_files_in_flight_are_claimed_once(library, monkeypatch):
    """The claim used to be taken after awaiting the DB, so both copies imported."""
    from backend.services import screenshot_service

    async def slow_check(_sha):
        await asyncio.sleep(0.01)
        return None

    monkeypatch.setattr(screenshot_service, "check_duplicate_hash", slow_check)
    claimed: set[str] = set()
    results = await asyncio.gather(*(
        screenshot_service.claim_unless_duplicate("sha:x", "x", claimed) for _ in range(5)
    ))
    assert sorted(results) == [False, True, True, True, True]
    assert claimed == {"sha:x"}


async def test_known_duplicate_releases_its_claim(library, monkeypatch):
    from backend.services import screenshot_service

    async def in_library(_sha):
        return {"id": 1}

    monkeypatch.setattr(screenshot_service, "check_duplicate_hash", in_library)
    claimed: set[str] = set()
    assert await screenshot_service.claim_unless_duplicate("sha:y", "y", claimed) is True
    assert claimed == set()


async def test_error_log_keeps_every_concurrent_message(library):
    from backend.database import get_db
    from backend.services import specialk_import_service, steam_import_service

    for module, table in ((steam_import_service, "steam_import_sessions"),
                          (specialk_import_service, "specialk_import_sessions")):
        db = await get_db()
        columns = {row[1]: row for row in await (await db.execute(f"PRAGMA table_info({table})")).fetchall()}
        required = [name for name, row in columns.items()
                    if row[3] and row[4] is None and not row[5]]
        values = {name: "x" for name in required}
        cursor = await db.execute(
            f"INSERT INTO {table} ({', '.join(values) or 'id'}) VALUES "
            f"({', '.join('?' for _ in values) or 'NULL'})",
            tuple(values.values()),
        )
        await db.commit()
        session_id = cursor.lastrowid

        async def append(i, module=module, session_id=session_id):
            await module.append_error_log(session_id, f"error {i}")

        # Separate tasks = separate pooled connections, like real import workers.
        await asyncio.gather(*(asyncio.create_task(append(i)) for i in range(12)))
        import json
        row = await (await db.execute(f"SELECT error_log FROM {table} WHERE id = ?", (session_id,))).fetchone()
        assert sorted(json.loads(row[0])) == sorted(f"error {i}" for i in range(12))


def test_specialk_copy_is_exclusive_and_never_a_hard_link(tmp_path):
    from backend.services.upload_service import _copy_exclusive

    source = tmp_path / "src.png"
    source.write_bytes(b"one")
    dest = tmp_path / "lib" / "a.png"
    dest.parent.mkdir()
    _copy_exclusive(source, dest, allow_link=False)
    assert dest.read_bytes() == b"one"
    assert dest.stat().st_nlink == 1  # editing the user's file can't change the library copy
    with pytest.raises(FileExistsError):
        _copy_exclusive(source, dest, allow_link=False)


# ── Steam scraper ───────────────────────────────────────────────────────────


async def test_steam_cookies_only_go_to_steamcommunity():
    from backend.services.steam_scraper import SteamScraper

    scraper = SteamScraper("someone", steam_login_secure="secret", session_id="sid")
    async with scraper:
        client = scraper._client
        community = client.build_request("GET", "https://steamcommunity.com/id/someone/screenshots")
        cdn = client.build_request("GET", "https://images.steamusercontent.com/ugc/1/")
        api = client.build_request("GET", "https://api.steampowered.com/ISteamUser/x")
    assert "steamLoginSecure=secret" in community.headers.get("cookie", "")
    assert "cookie" not in cdn.headers
    assert "cookie" not in api.headers


def test_vanity_names_are_url_encoded():
    from backend.services.steam_scraper import _get_profile_url

    assert _get_profile_url("a b/../x") == "https://steamcommunity.com/id/a%20b%2F..%2Fx"


# ── Login rate limiter ──────────────────────────────────────────────────────


def test_rate_limiter_forgets_ips_with_no_recent_failures():
    from backend.routers import auth

    bucket: dict[str, list[float]] = {"10.0.0.1": [0.0]}  # long expired
    auth._check_rate_limit(bucket, "10.0.0.1", "login")
    auth._check_rate_limit(bucket, "10.0.0.2", "login")
    assert bucket == {}


def test_entrypoint_skips_the_recursive_chown_when_ownership_is_right():
    script = (Path(__file__).parent.parent / "entrypoint.sh").read_text()
    assert "GAMEVAULT_FORCE_CHOWN" in script
    full_pass = [line for line in script.splitlines() if "chown -R gamevault:gamevault /data" in line]
    assert len(full_pass) == 1 and full_pass[0].startswith("    ")  # inside the if, not unconditional
