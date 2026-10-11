import ssl

import httpx
import pytest
from gamevault_sync import api


def test_candidate_urls():
    assert api.candidate_urls("tower:8080") == ["http://tower:8080", "https://tower:8080"]
    assert api.candidate_urls("192.168.1.20:8080/") == ["http://192.168.1.20:8080", "https://192.168.1.20:8080"]
    # A public name gets https only; http has to be asked for.
    assert api.candidate_urls("vault.example.com") == ["https://vault.example.com"]
    assert api.candidate_urls("http://vault.example.com") == ["http://vault.example.com"]
    assert api.candidate_urls("tower.tail1234.ts.net") == ["http://tower.tail1234.ts.net",
                                                           "https://tower.tail1234.ts.net"]
    assert api.candidate_urls("100.101.102.103:8080")[0] == "http://100.101.102.103:8080"
    assert api.candidate_urls("http://tower:8080/library") == ["http://tower:8080/library", "http://tower:8080"]
    assert api.candidate_urls("https://x.example/gv/api/health") == ["https://x.example/gv", "https://x.example"]
    assert api.candidate_urls("http://tower:8080/#/settings") == ["http://tower:8080"]
    assert api.candidate_urls("  ") == []


def _transport(routes):
    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key not in routes:
            return httpx.Response(404, json={"detail": "Not Found"})
        return routes[key](request)
    return httpx.MockTransport(handler)


def test_connect_finds_the_server_root_and_auth_mode():
    transport = _transport({
        ("GET", "/api/health"): lambda r: httpx.Response(200, json={"status": "ok", "version": "1.1.0"}),
        ("GET", "/api/auth/verify"): lambda r: httpx.Response(
            200, json={"authenticated": False, "setup_required": False, "auth_disabled": False}),
    })
    base, health, auth = api.connect("http://tower:8080/library", transport=transport)
    assert base == "http://tower:8080"
    assert health["version"] == "1.1.0"
    assert auth["auth_disabled"] is False


def test_connect_rejects_other_websites():
    transport = _transport({("GET", "/api/health"): lambda r: httpx.Response(200, text="<html>")})
    with pytest.raises(api.NotGameVaultError):
        api.connect("http://router.lan", transport=transport)


def _upload_with(body=None, status=200):
    transport = _transport({("POST", "/api/upload/sync"): lambda r: httpx.Response(status, json=body or {})})
    return api.GameVaultClient("http://x", "gvd_t", transport=transport)


@pytest.mark.parametrize("body,status,expected", [
    ({"uploaded": 1, "screenshots": [{}], "skipped": [], "errors": []}, 200, "uploaded"),
    ({"uploaded": 0, "skipped": [{"filename": "a.jpg", "reason": "Duplicate of b.jpg"}], "errors": []}, 200, "duplicate"),
    ({"uploaded": 0, "skipped": [], "errors": [{"filename": "a.jpg", "error": "Not a valid image file"}]}, 200, "rejected"),
    ({"uploaded": 0, "errors": [{"filename": "a.tga", "error": "Unsupported image format: TGA"}]}, 200, "rejected"),
    ({"detail": "File too big"}, 413, "rejected"),
    ({"detail": [{"msg": "field required"}]}, 422, "rejected"),
])
def test_upload_outcomes(tmp_path, body, status, expected):
    path = tmp_path / "a.jpg"
    path.write_bytes(b"x")
    with _upload_with(body, status) as client:
        assert client.upload(1, str(path), taken_at_hint="2024-01-01T00:00:00").outcome == expected


def test_upload_errors(tmp_path):
    path = tmp_path / "a.jpg"
    path.write_bytes(b"x")
    missing = {"uploaded": 0, "errors": [{"filename": None, "error": "Game ID 4 not found"}]}
    with _upload_with(missing) as client, pytest.raises(api.GameMissingError):
        client.upload(4, str(path))
    with _upload_with({"detail": "Invalid or expired token"}, 401) as client, pytest.raises(api.SignedOutError):
        client.upload(4, str(path))
    # Worth another try later, not a permanent "not accepted".
    for body, status in (({"detail": "boom"}, 500), ({"detail": "Slow down"}, 429), ({"detail": "Not Found"}, 404),
                         ({"uploaded": 0, "errors": [{"filename": "a.jpg", "error": "database is locked"}]}, 200)):
        with _upload_with(body, status) as client, pytest.raises(api.ApiError) as info:
            client.upload(4, str(path))
        assert type(info.value) is api.ApiError, body


def test_a_web_page_instead_of_the_api_is_explained(tmp_path):
    path = tmp_path / "a.jpg"
    path.write_bytes(b"x")
    transport = _transport({("POST", "/api/upload/sync"): lambda r: httpx.Response(200, text="<html>login</html>"),
                            ("POST", "/api/auth/devices/pair"): lambda r: httpx.Response(200, text="<html>")})
    with api.GameVaultClient("http://x", "gvd_t", transport=transport) as client:
        with pytest.raises(api.NotGameVaultError, match="login page or a proxy"):
            client.upload(4, str(path))
        with pytest.raises(api.NotGameVaultError):
            client.pair("pw", "PC")


def test_pairing_sends_the_install_id():
    seen = {}

    def pair(request):
        seen.update(__import__("json").loads(request.content))
        return httpx.Response(200, json={"token": "gvd_new", "device": {}})

    with api.GameVaultClient("http://x", transport=_transport({("POST", "/api/auth/devices/pair"): pair})) as client:
        assert client.pair("pw", "Gaming PC", "abc123") == "gvd_new"
    assert seen == {"password": "pw", "name": "Gaming PC", "install_id": "abc123"}


@pytest.mark.parametrize("status", [404, 405])
def test_a_server_without_pc_sign_ins_is_called_too_old(status):
    # GameVault 1.0.x behind its web app answers an unknown POST with 405.
    def old(request):
        return httpx.Response(status, json={"detail": "Method Not Allowed"})

    transport = _transport({("POST", "/api/auth/devices/pair"): old, ("POST", "/api/auth/devices"): old})
    with api.GameVaultClient("http://x", "eyJ.old", transport=transport) as client:
        with pytest.raises(api.ServerTooOldError, match="too old"):
            client.pair("pw", "PC")
        with pytest.raises(api.ServerTooOldError, match="too old"):
            client.create_device_token("PC")


def test_server_instance():
    transport = _transport({("GET", "/api/health"): lambda r: httpx.Response(
        200, json={"status": "ok", "version": "1.1.0", "instance": "a1b2c3d4e5f60718"})})
    with api.GameVaultClient("http://x", transport=transport) as client:
        assert client.server_instance() == "a1b2c3d4e5f60718"


def test_unreachable_server():
    def fail(request):
        raise httpx.ConnectError("refused", request=request)
    transport = httpx.MockTransport(fail)
    with api.GameVaultClient("http://x", "t", transport=transport) as client, pytest.raises(api.ServerUnreachableError):
        client.existing_hashes(["a"])


def test_untrusted_certificate_is_explained():
    def fail(request):
        try:
            raise ssl.SSLCertVerificationError("certificate verify failed: self-signed certificate")
        except ssl.SSLCertVerificationError as exc:
            raise httpx.ConnectError("handshake failed", request=request) from exc
    transport = httpx.MockTransport(fail)
    with api.GameVaultClient("https://x", "t", transport=transport) as client, pytest.raises(api.CertificateError):
        client.existing_hashes(["a"])
    with pytest.raises(api.CertificateError):
        api.connect("tower:8080", transport=transport)


def test_unreachable_public_name_suggests_http():
    def fail(request):
        raise httpx.ConnectError("refused", request=request)
    with pytest.raises(api.ServerUnreachableError, match="type http://"):
        api.connect("vault.example.com", transport=httpx.MockTransport(fail))
