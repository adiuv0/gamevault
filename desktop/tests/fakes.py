"""A fake Steam install, Special K folder and GameVault server for tests."""

from __future__ import annotations

import hashlib
import os
import struct
import time
from pathlib import Path

from gamevault_sync.api import (
    ApiError,
    GameMissingError,
    ServerTooOldError,
    ServerUnreachableError,
    SignedOutError,
    UploadOutcome,
)

OLD = time.time() - 3600  # comfortably older than the "still being written" window


def write_file(path: Path, data: bytes, mtime: float = OLD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))
    return path


def binary_vdf(shortcuts: dict[int, str]) -> bytes:
    out = b"\x00shortcuts\x00"
    for index, (app_id, name) in enumerate(shortcuts.items()):
        signed = struct.unpack("<i", struct.pack("<I", app_id))[0]
        out += b"\x00" + str(index).encode() + b"\x00"
        out += b"\x02appid\x00" + struct.pack("<i", signed)
        out += b"\x01AppName\x00" + name.encode() + b"\x00"
        out += b"\x01Exe\x00\"C:\\emu.exe\"\x00"
        out += b"\x00tags\x00\x08"
        out += b"\x08"
    return out + b"\x08\x08"


def _vdf_escape(path: Path) -> str:
    return str(path).replace("\\", "\\\\")


def make_steam(root: Path) -> Path:
    """Steam with one account: Elden Ring (installed in a second library),
    Portal (not installed) and a non-Steam game."""
    steam = root / "Steam"
    library2 = root / "Games" / "SteamLibrary"
    (steam / "steamapps").mkdir(parents=True)
    folders = "".join(f' "{i}" {{ "path" "{_vdf_escape(p)}" }}\n' for i, p in enumerate((steam, library2)))
    (steam / "steamapps" / "libraryfolders.vdf").write_text(f'"libraryfolders"\n{{\n{folders}}}\n',
                                                          encoding="utf-8")
    (library2 / "steamapps").mkdir(parents=True)
    (library2 / "steamapps" / "appmanifest_1245620.acf").write_text(
        '"AppState"\n{\n "appid" "1245620"\n "name" "ELDEN RING"\n}\n', encoding="utf-8")

    account = steam / "userdata" / "11111"
    remote = account / "760" / "remote"
    write_file(remote / "1245620" / "screenshots" / "20240301203000_1.jpg", b"elden-1")
    write_file(remote / "1245620" / "screenshots" / "20240301203512_1.jpg", b"elden-2")
    write_file(remote / "1245620" / "screenshots" / "thumbnails" / "20240301203000_1.jpg", b"thumb")
    write_file(remote / "400" / "screenshots" / "20110419101010_1.jpg", b"portal-1")
    write_file(remote / "400" / "screenshots" / "notes.txt", b"not an image")
    write_file(remote / "400" / "screenshots" / "old.tga", b"server would reject")
    write_file(remote / "3228583970" / "screenshots" / "20250105090000_1.png", b"emu-1")
    (account / "760" / "screenshots.vdf").write_text(
        '"Screenshots"\n{\n "11111"\n {\n  "400"\n  {\n   "0"\n   {\n'
        '    "filename" "400/screenshots/20110419101010_1.jpg"\n    "creation" "1303200000"\n'
        '   }\n  }\n }\n}\n', encoding="utf-8")
    write_file(account / "config" / "shortcuts.vdf", binary_vdf({3228583970: "Dolphin"}))
    return steam


def make_specialk(root: Path) -> Path:
    profiles = root / "My Mods" / "SpecialK" / "Profiles"
    write_file(profiles / "Cyberpunk2077.exe" / "Screenshots" / "HDR" / "2024-05-01 21-15-03.jxr", b"cp-hdr")
    write_file(profiles / "Cyberpunk2077.exe" / "Screenshots" / "SDR" / "2024-05-01 21-15-03.png", b"cp-sdr")
    write_file(profiles / "Cyberpunk2077.exe" / "SpecialK.ini", b"[ini]")
    write_file(profiles / "HollowKnight" / "shot.png", b"hk-1")
    (profiles / "EmptyGame").mkdir(parents=True)
    return profiles


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeServer:
    """Enough of GameVault for the engine: games, hashes, uploads."""

    def __init__(self):
        self.hashes: set[str] = set()
        self.games: dict[int, str] = {}
        self.steam_ids: dict[int, int] = {}
        self.uploads: list[tuple[int, str, str | None]] = []
        self.calls: list[str] = []
        self.reject: set[str] = set()  # filenames the server refuses
        self.fail: set[str] = set()  # filenames whose upload fails, for now
        self.offline = False
        self.signed_out = False
        self.too_old = False  # a GameVault 1.0.x server: no PC sign-ins
        self.instance: str | None = "fakeserver01"
        self.next_id = 1

    def client(self, server: str = "", token: str = "") -> FakeClient:
        return FakeClient(self)

    def new_game(self, name: str) -> int:
        game_id = self.next_id
        self.next_id += 1
        self.games[game_id] = name
        return game_id


class FakeClient:
    def __init__(self, server: FakeServer):
        self.s = server

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def close(self):
        pass

    def _guard(self, name: str) -> None:
        self.s.calls.append(name)
        if self.s.offline:
            raise ServerUnreachableError("Can't reach the fake server.")
        if self.s.signed_out:
            raise SignedOutError()

    def server_instance(self):
        self._guard("health")
        return self.s.instance

    def existing_hashes(self, hashes):
        self._guard("check")
        return {h for h in hashes if h in self.s.hashes}

    def game_by_steam_app_id(self, app_id, name_hint=None):
        self._guard("steam")
        if app_id not in self.s.steam_ids or self.s.steam_ids[app_id] not in self.s.games:
            # Like the real server: the Steam store's name, else the hint, else a placeholder.
            name = {400: "Portal"}.get(app_id) or name_hint or f"App {app_id}"
            self.s.steam_ids[app_id] = self.s.new_game(name)
        game_id = self.s.steam_ids[app_id]
        return {"id": game_id, "name": self.s.games[game_id]}

    def game_by_name(self, name):
        self._guard("name")
        for game_id, existing in self.s.games.items():
            if existing.lower() == name.lower():
                return {"id": game_id, "name": existing}
        return {"id": self.s.new_game(name), "name": name}

    def upload(self, game_id, path, *, filename=None, taken_at_hint=None):
        self._guard("upload")
        if game_id not in self.s.games:
            raise GameMissingError(f"Game ID {game_id} not found")
        name = filename or os.path.basename(path)
        if name in self.s.reject:
            return UploadOutcome("rejected", "Not a valid image file")
        if name in self.s.fail:
            raise ApiError("database is locked")
        digest = sha(Path(path).read_bytes())
        if digest in self.s.hashes:
            return UploadOutcome("duplicate", "Duplicate")
        self.s.hashes.add(digest)
        self.s.uploads.append((game_id, name, taken_at_hint))
        return UploadOutcome("uploaded")

    def create_device_token(self, name):
        self._guard("device")
        if self.s.too_old:
            raise ServerTooOldError()
        return "gvd_upgraded"

    def sign_out(self):
        self.s.calls.append("sign_out")
