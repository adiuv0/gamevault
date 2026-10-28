"""Sync-app device tokens: pairing, scope, revocation, and password changes."""

import httpx
import pytest


@pytest.fixture
async def server(monkeypatch, tmp_path):
    from backend import auth
    from backend.config import settings
    from backend.database import close_db, init_db
    from backend.main import app
    from backend.routers import auth as auth_router

    await close_db()
    for field, path in {"data_dir": tmp_path, "db_path": tmp_path / "state.db",
                        "library_dir": tmp_path / "library", "thumbnail_dir": tmp_path / "cache"}.items():
        monkeypatch.setattr(settings, field, path)
    monkeypatch.setattr(settings, "disable_auth", False)
    auth_router._login_attempts.clear()
    await init_db()
    await auth.set_password("correct horse")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    auth_router._login_attempts.clear()
    await close_db()


async def _pair(client, name="Gaming PC", password="correct horse", install_id="install-1"):
    return await client.post("/api/auth/devices/pair",
                             json={"password": password, "name": name, "install_id": install_id})


async def _session(client):
    response = await client.post("/api/auth/login", json={"password": "correct horse"})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def test_pairing_needs_the_password(server):
    assert (await _pair(server, password="wrong")).status_code == 401
    response = await _pair(server)
    assert response.status_code == 200
    body = response.json()
    assert body["token"].startswith("gvd_")
    assert body["device"]["name"] == "Gaming PC"


async def test_device_token_reaches_only_sync_endpoints(server):
    token = (await _pair(server)).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    verify = await server.get("/api/auth/verify", headers=headers)
    assert verify.json()["authenticated"] is True
    check = await server.post("/api/screenshots/check-hashes", json={"hashes": ["ab" * 32]},
                              headers=headers)
    assert check.status_code == 200
    game = await server.post("/api/games/by-name", json={"name": "Elden Ring"}, headers=headers)
    assert game.status_code == 200

    for method, path in (("GET", "/api/games"), ("GET", "/api/settings"),
                         ("DELETE", f"/api/games/{game.json()['id']}"),
                         ("GET", "/api/auth/devices")):
        response = await server.request(method, path, headers=headers)
        assert response.status_code == 403, (method, path)

    # Never accepted from the query string, even on media paths that allow ?token=.
    assert (await server.get(f"/api/games/{game.json()['id']}/cover?token={token}")).status_code == 401


async def test_revoked_and_repaired_devices_lose_access(server):
    first = (await _pair(server)).json()
    second = (await _pair(server)).json()  # same PC signs in again
    session = await _session(server)
    devices = (await server.get("/api/auth/devices", headers=session)).json()["devices"]
    assert [d["id"] for d in devices] == [second["device"]["id"]]

    old = {"Authorization": f"Bearer {first['token']}"}
    assert (await server.post("/api/screenshots/check-hashes", json={"hashes": []},
                              headers=old)).status_code == 401

    new = {"Authorization": f"Bearer {second['token']}"}
    # Another PC with the same name is a different install and keeps its sign-in.
    twin = (await _pair(server, install_id="install-2")).json()
    devices = (await server.get("/api/auth/devices", headers=session)).json()["devices"]
    assert {d["id"] for d in devices} == {second["device"]["id"], twin["device"]["id"]}

    revoke = await server.delete(f"/api/auth/devices/{second['device']['id']}", headers=session)
    assert revoke.status_code == 200
    assert (await server.post("/api/screenshots/check-hashes", json={"hashes": []},
                              headers=new)).status_code == 401


async def test_device_can_sign_itself_out(server):
    pair = (await _pair(server)).json()
    headers = {"Authorization": f"Bearer {pair['token']}"}
    assert (await server.delete("/api/auth/devices/current", headers=headers)).status_code == 200
    assert (await server.get("/api/auth/verify", headers=headers)).json()["authenticated"] is False
    session = await _session(server)
    assert (await server.get("/api/auth/devices", headers=session)).json()["devices"] == []
    # A browser session has no device to sign out.
    assert (await server.delete("/api/auth/devices/current", headers=session)).status_code == 400


async def test_steam_lookup_uses_the_local_name_when_the_store_is_unreachable(server, monkeypatch):
    from backend.services import metadata_service

    async def store_down(app_id):
        raise RuntimeError("no network")

    monkeypatch.setattr(metadata_service, "fetch_steam_metadata", store_down)
    headers = {"Authorization": f"Bearer {(await _pair(server)).json()['token']}"}
    placeholder = await server.get("/api/games/by-steam-appid/1245620", headers=headers)
    assert placeholder.json()["name"] == "App 1245620"
    named = await server.get("/api/games/by-steam-appid/1245620", params={"name": " ELDEN  RING "},
                             headers=headers)
    assert named.json()["id"] == placeholder.json()["id"]
    assert named.json()["name"] == "ELDEN RING"
    fresh = await server.get("/api/games/by-steam-appid/367520", params={"name": "Hollow Knight"},
                             headers=headers)
    assert fresh.json()["name"] == "Hollow Knight"


async def test_password_change_signs_out_devices(server):
    token = (await _pair(server)).json()["token"]
    session = await _session(server)
    response = await server.post("/api/auth/change-password", headers=session,
                                 json={"current_password": "correct horse",
                                       "new_password": "battery staple"})
    assert response.status_code == 200
    headers = {"Authorization": f"Bearer {token}"}
    assert (await server.post("/api/screenshots/check-hashes", json={"hashes": []},
                              headers=headers)).status_code == 401


async def test_web_session_can_create_a_token(server):
    session = await _session(server)
    created = await server.post("/api/auth/devices", json={"name": "NAS script"}, headers=session)
    token = created.json()["token"]
    check = await server.post("/api/screenshots/check-hashes", json={"hashes": []},
                              headers={"Authorization": f"Bearer {token}"})
    assert check.status_code == 200


async def test_sync_upload_reports_outcomes_and_uses_date_hint(server):
    from io import BytesIO

    from PIL import Image

    token = (await _pair(server)).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    game = (await server.post("/api/games/by-name", json={"name": "Hint Test"},
                              headers=headers)).json()
    buffer = BytesIO()
    Image.new("RGB", (16, 9), "orange").save(buffer, "PNG")
    png = buffer.getvalue()

    async def upload(name, data):
        return (await server.post(
            "/api/upload/sync", headers=headers,
            data={"game_id": str(game["id"]), "taken_at_hint": "2024-05-12T21:30:00"},
            files={"files": (name, data, "image/png")},
        )).json()

    first = await upload("shot.png", png)
    assert first["uploaded"] == 1 and not first["skipped"] and not first["errors"]
    assert first["screenshots"][0]["taken_at"].startswith("2024-05-12T21:30")

    again = await upload("shot-copy.png", png)
    assert again["uploaded"] == 0
    assert again["skipped"][0]["reason"].startswith("Duplicate")

    junk = await upload("notes.png", b"not an image at all")
    assert junk["uploaded"] == 0 and junk["errors"][0]["error"] == "Not a valid image file"


async def test_health_names_the_server_instance(server):
    first = (await server.get("/api/health")).json()
    second = (await server.get("/api/health")).json()
    assert first["status"] == "ok"
    assert len(first["instance"]) == 16 and first["instance"] == second["instance"]
