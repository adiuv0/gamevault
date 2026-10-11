"""Talking to a GameVault server."""

from __future__ import annotations

import ipaddress
import logging
import mimetypes
import os
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from . import __version__

log = logging.getLogger(__name__)

_USER_AGENT = f"GameVaultSync/{__version__}"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# A big HDR capture is tone-mapped and thumbnailed before the server answers.
_UPLOAD_TIMEOUT = httpx.Timeout(600.0, connect=10.0)

_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
    ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff", ".gif": "image/gif",
    ".jxr": "image/vnd.ms-photo",
}


class ApiError(Exception):
    """Something the user can read went wrong talking to the server."""


class ServerUnreachableError(ApiError):
    pass


class NotGameVaultError(ApiError):
    pass


class SignedOutError(ApiError):
    def __init__(self, message: str = "This PC is signed out of GameVault.") -> None:
        super().__init__(message)


class SetupRequiredError(ApiError):
    pass


class GameMissingError(ApiError):
    """The game id the app remembered no longer exists on the server."""


class CertificateError(ApiError):
    """HTTPS works, but this PC doesn't trust the server's certificate."""


class ServerTooOldError(ApiError):
    """The server predates PC sign-ins (GameVault 1.0.x)."""

    def __init__(self, message: str = "This GameVault server is too old for this app. "
                                      "Update GameVault on the server first.") -> None:
        super().__init__(message)


# How a server without the PC sign-in routes answers them. Behind its web app,
# an unknown POST matches the page route and gets 405, not 404.
_NO_SUCH_ROUTE = frozenset({404, 405})


@dataclass(frozen=True)
class UploadOutcome:
    outcome: str  # "uploaded" | "duplicate" | "rejected"
    detail: str | None = None


# Per-file errors that sending the same file again can't fix. Anything else
# (a full disk, a busy database, an error nobody planned for) is tried again
# later, with growing gaps, instead of being written off.
_PERMANENT_ERRORS = ("not a valid image", "unsupported image format", "cannot identify image file")
# Statuses that mean "this request, as sent, will never work".
_REJECTED_STATUSES = {400, 413, 415, 422}

_verify: ssl.SSLContext | bool | None = None


def _ssl_verify() -> ssl.SSLContext | bool:
    """Trust the certificates the OS trusts (a home CA, a company proxy), not just certifi's list."""
    global _verify
    if _verify is None:
        try:
            import truststore

            _verify = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except Exception:
            log.info("truststore unavailable; using certifi's certificates", exc_info=True)
            _verify = True
    return _verify


def _is_certificate_problem(exc: BaseException) -> bool:
    current: BaseException | None = exc
    for _ in range(8):  # httpx wraps httpcore, which wraps the ssl error
        if current is None:
            return False
        if isinstance(current, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(current):
            return True
        current = current.__cause__ or current.__context__
    return False


def _transport_error(where: str, exc: httpx.TransportError, unreachable: str | None = None) -> ApiError:
    if isinstance(exc, httpx.TimeoutException):
        return ServerUnreachableError(f"{where} took too long to answer.")
    if _is_certificate_problem(exc):
        return CertificateError(
            f"This PC doesn't trust the HTTPS certificate of {where}. For a server at home, use its "
            "http:// address; otherwise add the certificate to the PC's trusted certificates.")
    return ServerUnreachableError(unreachable or f"Can't reach {where}.")


# ── Server addresses ─────────────────────────────────────────────────────────


_TAILNET = ipaddress.ip_network("100.64.0.0/10")


def _looks_local(host: str) -> bool:
    """A home-network or Tailscale name, where plain http is normal."""
    if "." not in host or host.endswith((".local", ".lan", ".home", ".internal", ".ts.net")):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address in _TAILNET


def candidate_urls(raw: str) -> list[str]:
    """Addresses worth trying for what someone typed or pasted.

    ``tower:8080`` gets http, then https (a home server rarely has a
    certificate). ``vault.example.com`` gets https only: falling back to http
    there would send the password in the clear to whoever broke https.
    Typing ``http://`` explicitly still works. A pasted page address such as
    ``http://tower:8080/library`` is tried as typed and then at its root.
    """
    text = raw.strip().strip("/")
    if not text:
        return []
    if "://" not in text:
        host = urlsplit("//" + text).hostname or ""
        schemes = ("http", "https") if _looks_local(host) else ("https",)
        texts = [f"{scheme}://{text}" for scheme in schemes]
    else:
        texts = [text]
    out: list[str] = []
    for item in texts:
        parts = urlsplit(item)
        path = parts.path.rstrip("/")
        if "/api/" in path + "/":
            path = path[: (path + "/").index("/api/")]
        for candidate_path in (path, ""):
            url = urlunsplit((parts.scheme, parts.netloc, candidate_path, "", "")).rstrip("/")
            if url not in out:
                out.append(url)
    return out


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except Exception:
        detail = None
    if isinstance(detail, str) and detail:
        return detail
    return f"HTTP {response.status_code}"


# ── Client ───────────────────────────────────────────────────────────────────


class GameVaultClient:
    def __init__(self, server: str, token: str = "", *, transport: httpx.BaseTransport | None = None):
        self.server = server.rstrip("/")
        headers = {"User-Agent": _USER_AGENT}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(base_url=self.server, headers=headers, timeout=_TIMEOUT,
                                  transport=transport, verify=_ssl_verify())

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GameVaultClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _request(self, method: str, path: str, *, signed_out_on_401: bool = True,
                 **kwargs) -> httpx.Response:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise _transport_error(self.server, exc) from exc
        if response.status_code == 401 and signed_out_on_401:
            raise SignedOutError()
        if response.status_code == 403 and _detail(response) == "setup_required":
            raise SetupRequiredError("GameVault needs a password first. Open it in a browser to set one.")
        return response

    def _body(self, response: httpx.Response) -> dict:
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise NotGameVaultError(
                f"{self.server} answered with a web page instead of GameVault. "
                "Is a login page or a proxy in the way?")
        return body

    def _json(self, method: str, path: str, **kwargs) -> dict:
        return self._checked(self._request(method, path, **kwargs), path)

    def _checked(self, response: httpx.Response, path: str) -> dict:
        if response.status_code == 403:
            raise ApiError(f"The server refused this: {_detail(response)}")
        if response.status_code >= 400:
            raise ApiError(f"The server answered {_detail(response)} for {path}.")
        return self._body(response)

    # ── Sign-in ──────────────────────────────────────────────────────────────

    def health(self) -> dict:
        try:
            response = self._http.get("/api/health")
        except httpx.TransportError as exc:
            raise _transport_error(self.server, exc) from exc
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code != 200 or not isinstance(body, dict) or body.get("status") != "ok":
            raise NotGameVaultError(f"{self.server} doesn't look like a GameVault server.")
        return body

    def server_instance(self) -> str | None:
        """The server's own id, which stays the same whatever address reaches it."""
        instance = self.health().get("instance")
        return instance if isinstance(instance, str) and instance else None

    def auth_status(self) -> dict:
        """``{authenticated, setup_required, auth_disabled}`` for the current token."""
        return self._json("GET", "/api/auth/verify")

    def pair(self, password: str, device_name: str, install_id: str = "") -> str:
        payload = {"password": password, "name": device_name}
        if install_id:
            payload["install_id"] = install_id
        response = self._request("POST", "/api/auth/devices/pair", signed_out_on_401=False, json=payload)
        if response.status_code == 401:
            raise ApiError("Wrong password.")
        if response.status_code == 429:
            raise ApiError("Too many wrong passwords. Wait 15 minutes and try again.")
        if response.status_code in _NO_SUCH_ROUTE:
            raise ServerTooOldError()
        if response.status_code >= 400:
            raise ApiError(_detail(response))
        token = self._body(response).get("token")
        if not isinstance(token, str) or not token:
            raise NotGameVaultError(f"{self.server} didn't send a sign-in back.")
        return token

    def create_device_token(self, device_name: str) -> str:
        """Swap a browser session token for a device token (old installs)."""
        response = self._request("POST", "/api/auth/devices", json={"name": device_name})
        if response.status_code in _NO_SUCH_ROUTE:
            raise ServerTooOldError()
        return self._checked(response, "/api/auth/devices")["token"]

    def sign_out(self) -> None:
        try:
            self._request("DELETE", "/api/auth/devices/current")
        except ApiError:
            pass

    # ── Sync ─────────────────────────────────────────────────────────────────

    def existing_hashes(self, hashes: list[str]) -> set[str]:
        body = self._json("POST", "/api/screenshots/check-hashes", json={"hashes": hashes})
        return set(body.get("existing", []))

    def game_by_steam_app_id(self, app_id: int, name_hint: str | None = None) -> dict:
        params = {"name": name_hint} if name_hint else None
        return self._json("GET", f"/api/games/by-steam-appid/{app_id}", params=params)

    def game_by_name(self, name: str) -> dict:
        return self._json("POST", "/api/games/by-name", json={"name": name})

    def upload(self, game_id: int, path: str, *, filename: str | None = None,
               taken_at_hint: str | None = None) -> UploadOutcome:
        filename = filename or os.path.basename(path)
        ext = os.path.splitext(filename)[1].lower()
        mime = _MIME.get(ext) or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        data = {"game_id": str(game_id)}
        if taken_at_hint:
            data["taken_at_hint"] = taken_at_hint
        with open(path, "rb") as fh:
            response = self._request("POST", "/api/upload/sync", data=data,
                                     files={"files": (filename, fh, mime)}, timeout=_UPLOAD_TIMEOUT)
        if response.status_code == 413:
            return UploadOutcome("rejected", "Bigger than the server's upload limit")
        if response.status_code in _REJECTED_STATUSES:
            return UploadOutcome("rejected", _detail(response))
        if response.status_code >= 400:
            # 5xx, 404 from a proxy, 408, 429…: worth another try later.
            raise ApiError(f"The server answered {_detail(response)} while saving {filename}.")
        body = self._body(response)
        if body.get("uploaded", 0) > 0:
            return UploadOutcome("uploaded")
        for error in body.get("errors") or []:
            message = str(error.get("error") or "Not accepted")
            if error.get("filename") is None and "not found" in message.lower():
                raise GameMissingError(message)
            if message.lower().startswith(_PERMANENT_ERRORS):
                return UploadOutcome("rejected", message)
            raise ApiError(message)
        for skipped in body.get("skipped") or []:
            return UploadOutcome("duplicate", skipped.get("reason"))
        raise ApiError("The server didn't say what happened to the file.")


def connect(raw: str, *, transport: httpx.BaseTransport | None = None) -> tuple[str, dict, dict]:
    """Find the GameVault server behind what the user typed.

    Returns ``(base_url, health, auth_status)``. Redirects (http to https) are
    followed and the final address is kept.
    """
    candidates = candidate_urls(raw)
    if not candidates:
        raise ApiError("Enter your GameVault server's address.")
    typed = raw.strip()
    unreachable = f"Can't reach {typed}. Check the address and that the server is running."
    if not any(url.startswith("http:") for url in candidates):
        unreachable += " If it only answers on http, type http:// in front of the address."
    last: ApiError | None = None
    for url in candidates:
        try:
            with httpx.Client(timeout=httpx.Timeout(8.0, connect=5.0), follow_redirects=True,
                              headers={"User-Agent": _USER_AGENT}, transport=transport,
                              verify=_ssl_verify()) as http:
                response = http.get(url + "/api/health")
            body = response.json() if response.status_code == 200 else None
        except httpx.TransportError as exc:
            error = _transport_error(typed, exc, unreachable)
            # The first "can't reach" is the useful one, but a certificate problem says more.
            if last is None or (type(last) is ServerUnreachableError and type(error) is not ServerUnreachableError):
                last = error
            continue
        except ValueError:
            body = None
        if not isinstance(body, dict) or body.get("status") != "ok":
            last = NotGameVaultError(f"{raw.strip()} answered, but it doesn't look like GameVault.")
            continue
        final = str(response.url)
        base = final[: -len("/api/health")] if final.endswith("/api/health") else url
        with GameVaultClient(base, transport=transport) as client:
            status = client.auth_status()
        return base.rstrip("/"), body, status
    assert last is not None
    raise last
