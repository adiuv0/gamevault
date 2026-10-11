"""Settings, with the sign-in token kept in the OS keyring.

The token goes to Credential Manager on Windows (Keychain on macOS, Secret
Service on Linux) so it never sits in a plaintext file. When no keyring is
usable, it falls back to the JSON file with a one-time warning (GV-011).

``load_config``/``save_config`` work on plain dicts and are what the tests and
the legacy command line use; the app itself goes through ``Settings``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import app_paths

log = logging.getLogger(__name__)

CONFIG_PATH = app_paths.data_dir(create=False) / "config.json"

_KEYRING_SERVICE = "gamevault_sync"
_KEYRING_USERNAME = "token"

# Set in the JSON when the token lives in the keyring, so a missing keyring
# entry is noticed instead of silently looking signed out.
_KEYRING_FLAG_KEY = "_token_in_keyring"

_keyring_warning_shown = False


def _get_keyring_module():
    """Import keyring lazily; None when it is missing or has no usable backend."""
    try:
        import keyring  # type: ignore

        return keyring
    except Exception:
        return None


def _save_token_to_keyring(token: str) -> bool:
    kr = _get_keyring_module()
    if kr is None:
        return False
    try:
        kr.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, token)
        return True
    except Exception as exc:
        log.warning("keyring rejected the token: %s", exc)
        return False


def _load_token_from_keyring() -> str | None:
    kr = _get_keyring_module()
    if kr is None:
        return None
    try:
        return kr.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    except Exception:
        return None


def _delete_token_from_keyring() -> None:
    kr = _get_keyring_module()
    if kr is None:
        return
    try:
        kr.delete_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    except Exception:
        pass


def _warn_plaintext_once() -> None:
    global _keyring_warning_shown
    if _keyring_warning_shown:
        return
    _keyring_warning_shown = True
    hint = "" if _get_keyring_module() else "\n      For keyring storage, run:  pip install keyring"
    print(f"NOTE: No usable OS keyring, so the sign-in token is stored in {CONFIG_PATH}.{hint}",
          file=sys.stderr)
    log.warning("no usable keyring; the sign-in token is stored in %s", CONFIG_PATH)


def load_config() -> dict:
    """Read the saved config, pulling the token back out of the keyring."""
    cfg: dict = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    if cfg.get(_KEYRING_FLAG_KEY):
        token = _load_token_from_keyring()
        if token:
            cfg["token"] = token
        else:
            cfg.pop(_KEYRING_FLAG_KEY, None)
    return cfg


def save_config(cfg: dict) -> None:
    """Write the config; the token goes to the keyring when one is usable."""
    out = dict(cfg)
    token = out.pop("token", None) or ""
    if token:
        if _save_token_to_keyring(token):
            out[_KEYRING_FLAG_KEY] = True
        else:
            _warn_plaintext_once()
            out["token"] = token
            out.pop(_KEYRING_FLAG_KEY, None)
    else:
        # Signing out clears both stores.
        out.pop(_KEYRING_FLAG_KEY, None)
        _delete_token_from_keyring()
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)


@dataclass
class Settings:
    server: str = ""
    token: str = ""
    # The server's own id (from /api/health), so a new address for the same
    # server keeps what the app remembers and a different server starts fresh.
    server_id: str = ""
    device_name: str = ""
    # A random id for this install; signing in again replaces only its own token.
    install_id: str = ""
    steam_enabled: bool = True
    steam_path: str = ""  # empty: detect automatically
    specialk_enabled: bool = True
    specialk_path: str = ""  # empty: detect automatically
    auto_sync: bool = True
    interval_seconds: int = 60
    notify: bool = True
    # The first sync waits for a click, so nobody is surprised by 4,000 uploads.
    first_sync_confirmed: bool = False
    excluded_games: list[str] = field(default_factory=list)
    # Start with Windows is turned on once, at the first sign-in; after that
    # it's the user's choice.
    autostart_initialized: bool = False

    @property
    def signed_in(self) -> bool:
        return bool(self.server and self.token)

    @property
    def server_key(self) -> str:
        """Names this server's sync memory: its id, or its address when the id is unknown."""
        server_id = re.sub(r"[^A-Za-z0-9]", "", self.server_id)[:32]
        if server_id:
            return server_id
        return "url-" + hashlib.sha256(self.server.rstrip("/").lower().encode("utf-8")).hexdigest()[:16]

    @classmethod
    def from_dict(cls, data: dict) -> Settings:
        known = {f.name for f in fields(cls)}
        settings = cls(**{k: v for k, v in data.items() if k in known})
        settings.excluded_games = [str(k) for k in settings.excluded_games or []]
        try:
            settings.interval_seconds = max(15, int(settings.interval_seconds))
        except (TypeError, ValueError):
            settings.interval_seconds = 60
        return settings

    @classmethod
    def load(cls) -> Settings:
        if not CONFIG_PATH.exists() and app_paths.LEGACY_CONFIG_PATH.exists():
            migrated = _migrate_legacy()
            if migrated is not None:
                return migrated
        return cls.from_dict(load_config())

    def save(self) -> None:
        save_config(asdict(self))

    def sign_in_changes(self, *, server: str, token: str, device_name: str, server_id: str = "",
                        install_id: str = "") -> dict:
        """The settings a new sign-in changes.

        Signing in to the same server again, at any address, keeps everything.
        Another server's first sync waits for a click again.
        """
        if server_id and self.server_id:
            same_server = server_id == self.server_id
        else:
            same_server = server.rstrip("/").lower() == self.server.rstrip("/").lower()
        changes: dict = {"server": server, "token": token, "device_name": device_name, "server_id": server_id}
        if install_id:
            changes["install_id"] = install_id
        if not same_server:
            changes["first_sync_confirmed"] = False
        return changes


def _migrate_legacy() -> Settings | None:
    """Carry the old script's server and folders over; its token too, for now.

    The old token is a browser session that expires; the app swaps it for a
    device token on its first successful sync.
    """
    global CONFIG_PATH
    try:
        legacy_path = app_paths.LEGACY_CONFIG_PATH
        current = CONFIG_PATH
        CONFIG_PATH = legacy_path
        try:
            old = load_config()
        finally:
            CONFIG_PATH = current
    except Exception:
        log.exception("could not read the old config")
        return None
    mode = old.get("mode", "steam")
    settings = Settings(
        server=old.get("server", ""),
        token=old.get("token", ""),
        steam_enabled=mode in ("steam", "both"),
        steam_path=old.get("steam_path", ""),
        specialk_enabled=mode in ("specialk", "both") or bool(old.get("specialk_path")),
        specialk_path=old.get("specialk_path", ""),
        # Someone who ran the old tool has already chosen to upload everything.
        first_sync_confirmed=bool(old.get("server")),
    )
    settings.save()
    _drop_legacy_token(legacy_path)
    log.info("migrated settings from %s", legacy_path)
    return settings


def _drop_legacy_token(path: Path) -> None:
    """Leave no second copy of the sign-in in the old script's file."""
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(old, dict) or not ({"token", _KEYRING_FLAG_KEY} & old.keys()):
            return
        old.pop("token", None)
        old.pop(_KEYRING_FLAG_KEY, None)  # the keyring entry now belongs to the app
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(old, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        log.warning("couldn't remove the token from %s", path, exc_info=True)


def config_path() -> Path:
    return CONFIG_PATH
