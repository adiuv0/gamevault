"""Find screenshot folders on this PC and list the captures in them.

Steam keeps screenshots per Steam account under
``<Steam>/userdata/<account>/760/remote/<appid>/screenshots``. Special K keeps
one folder per game under its ``Profiles`` folder, with HDR captures as .jxr.
Scanning only stats files; hashing happens later, and only for files the sync
memory has not seen before.
"""

from __future__ import annotations

import logging
import os
import re
import struct
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .vdf import vdf_parse

log = logging.getLogger(__name__)

# What the server accepts (backend ALLOWED_IMAGE_FORMATS); anything else would be
# rejected after a pointless upload.
STEAM_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SPECIALK_EXTENSIONS = {".jxr", ".png"}

# Shortcut ("non-Steam game") ids have the top bit set; real app ids never do.
_SHORTCUT_ID_FLOOR = 0x80000000


@dataclass(frozen=True)
class GameRef:
    """One game as this PC knows it.

    ``key`` is stable across runs and is what the exclusion list stores. When
    ``steam_app_id`` is set the server resolves the game by app id (and fetches
    its proper name); otherwise it is found or created by ``name``.
    """

    key: str
    source: str  # "steam" | "specialk"
    name: str
    steam_app_id: int | None = None

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        if self.steam_app_id is not None:
            return f"Steam app {self.steam_app_id}"
        return self.key.split(":", 1)[-1]


@dataclass
class LocalFile:
    path: str
    size: int
    mtime_ns: int
    game: GameRef
    taken_at_hint: str | None = None

    @property
    def filename(self) -> str:
        return os.path.basename(self.path)


@dataclass
class ScanResult:
    files: list[LocalFile] = field(default_factory=list)
    games: dict[str, GameRef] = field(default_factory=dict)
    steam_root: Path | None = None
    specialk_root: Path | None = None
    problems: list[str] = field(default_factory=list)

    def add(self, item: LocalFile) -> None:
        self.files.append(item)
        self.games.setdefault(item.game.key, item.game)


# ── Capture dates ────────────────────────────────────────────────────────────

_STEAM_NAME_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})_\d+")
_GENERIC_NAME_DATE = re.compile(
    r"(\d{4})[-_.](\d{2})[-_.](\d{2})[ _T-](\d{2})[-_.:h](\d{2})[-_.:m](\d{2})"
)


def _iso_local(timestamp: float) -> str | None:
    try:
        return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def date_from_filename(filename: str) -> str | None:
    for pattern in (_STEAM_NAME_DATE, _GENERIC_NAME_DATE):
        match = pattern.search(filename)
        if match:
            try:
                return datetime(*(int(part) for part in match.groups())).isoformat()
            except ValueError:
                continue
    return None


def capture_date_hint(filename: str, mtime: float, steam_creation: int | None = None) -> str | None:
    """Best guess at when a capture was taken, as local time.

    The server prefers a date in the image's own EXIF; this matters for PNG and
    JXR captures, which have none.
    """
    if steam_creation:
        hint = _iso_local(steam_creation)
        if hint:
            return hint
    return date_from_filename(filename) or _iso_local(mtime)


# ── Finding folders ──────────────────────────────────────────────────────────


def _registry_value(root_name: str, subkey: str, value: str) -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg

        root = getattr(winreg, root_name)
        with winreg.OpenKey(root, subkey) as key:
            data, _ = winreg.QueryValueEx(key, value)
        return os.path.expandvars(str(data)) if data else None
    except OSError:
        return None


def _documents_dir() -> Path:
    """The real Documents folder, which OneDrive often moves."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                            ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]

            # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
            folder_id = GUID(0xFDD39AD0, 0x238F, 0x46AF,
                             (wintypes.BYTE * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
            out = ctypes.c_wchar_p()
            shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
            if shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(out)) == 0:
                path = out.value
                ctypes.windll.ole32.CoTaskMemFree(out)  # type: ignore[attr-defined]
                if path:
                    return Path(path)
        except Exception:
            pass
        personal = _registry_value(
            "HKEY_CURRENT_USER",
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            "Personal",
        )
        if personal:
            return Path(personal)
    return Path.home() / "Documents"


def steam_candidates() -> list[Path]:
    found: list[Path] = []
    for root_name, subkey, value in (
        ("HKEY_CURRENT_USER", r"Software\Valve\Steam", "SteamPath"),
        ("HKEY_LOCAL_MACHINE", r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        ("HKEY_LOCAL_MACHINE", r"SOFTWARE\Valve\Steam", "InstallPath"),
    ):
        path = _registry_value(root_name, subkey, value)
        if path:
            found.append(Path(path))
    home = Path.home()
    if sys.platform == "win32":
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            if os.environ.get(env):
                found.append(Path(os.environ[env]) / "Steam")
        found += [Path(r"C:\Program Files (x86)\Steam"), Path(r"D:\Steam"),
                  Path(r"D:\SteamLibrary\Steam")]
    elif sys.platform == "darwin":
        found.append(home / "Library" / "Application Support" / "Steam")
    else:
        found += [home / ".steam" / "steam", home / ".local" / "share" / "Steam",
                  home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam"]
    return found


def find_steam_root() -> Path | None:
    seen: set[str] = set()
    for path in steam_candidates():
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        if (path / "userdata").is_dir():
            return path
    return None


def specialk_candidates() -> list[Path]:
    found: list[Path] = []
    skif = _registry_value("HKEY_CURRENT_USER", r"Software\Kaldaien\Special K", "Path")
    if skif:
        found.append(Path(skif) / "Profiles")
    found.append(_documents_dir() / "My Mods" / "SpecialK" / "Profiles")
    if sys.platform == "win32":
        for env in ("ProgramFiles", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                sub = ("Programs", "Special K") if env == "LOCALAPPDATA" else ("Special K",)
                found.append(Path(base).joinpath(*sub, "Profiles"))
    return found


def find_specialk_root() -> Path | None:
    for path in specialk_candidates():
        if path.is_dir():
            return path
    return None


# ── Steam ────────────────────────────────────────────────────────────────────


def steam_account_ids(steam_root: Path) -> list[str]:
    userdata = steam_root / "userdata"
    try:
        return sorted(e.name for e in os.scandir(userdata) if e.is_dir() and e.name.isdigit())
    except OSError:
        return []


def _steam_library_dirs(steam_root: Path) -> list[Path]:
    dirs = [steam_root / "steamapps"]
    vdf_path = steam_root / "steamapps" / "libraryfolders.vdf"
    try:
        data = vdf_parse(vdf_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return dirs
    folders = data.get("libraryfolders") or data.get("LibraryFolders") or {}
    for value in folders.values():
        path = value.get("path") if isinstance(value, dict) else value
        if isinstance(path, str) and path:
            candidate = Path(path) / "steamapps"
            if candidate not in dirs:
                dirs.append(candidate)
    return dirs


def steam_installed_names(steam_root: Path) -> dict[int, str]:
    """App names from the appmanifests of installed games, across all libraries."""
    names: dict[int, str] = {}
    for library in _steam_library_dirs(steam_root):
        try:
            entries = list(os.scandir(library))
        except OSError:
            continue
        for entry in entries:
            if not (entry.name.startswith("appmanifest_") and entry.name.endswith(".acf")):
                continue
            try:
                with open(entry.path, encoding="utf-8", errors="replace") as fh:
                    state = vdf_parse(fh.read()).get("AppState", {})
                names[int(state["appid"])] = str(state["name"]).strip()
            except Exception:
                continue
    return names


def _read_cstring(data: bytes, pos: int) -> tuple[str, int]:
    end = data.index(b"\x00", pos)
    return data[pos:end].decode("utf-8", errors="replace"), end + 1


def parse_binary_vdf(data: bytes) -> dict:
    """Parse Steam's binary KeyValues (shortcuts.vdf)."""

    def parse_map(pos: int) -> tuple[dict, int]:
        out: dict = {}
        while True:
            kind = data[pos]
            pos += 1
            if kind in (0x08, 0x0B):
                return out, pos
            name, pos = _read_cstring(data, pos)
            if kind == 0x00:
                out[name], pos = parse_map(pos)
            elif kind == 0x01:
                out[name], pos = _read_cstring(data, pos)
            elif kind in (0x02, 0x03, 0x04, 0x06):
                out[name] = struct.unpack_from("<i" if kind != 0x03 else "<f", data, pos)[0]
                pos += 4
            elif kind in (0x07, 0x0A):
                out[name] = struct.unpack_from("<Q" if kind == 0x07 else "<q", data, pos)[0]
                pos += 8
            else:
                raise ValueError(f"unknown binary VDF type {kind:#x}")

    result, _ = parse_map(0)
    return result


def steam_shortcut_names(steam_root: Path, account_id: str) -> dict[int, str]:
    """Names of non-Steam games added to this Steam account, keyed by screenshot id."""
    path = steam_root / "userdata" / account_id / "config" / "shortcuts.vdf"
    try:
        data = parse_binary_vdf(path.read_bytes())
    except (OSError, ValueError, IndexError, struct.error):
        return {}
    names: dict[int, str] = {}
    shortcuts = data.get("shortcuts") or data.get("Shortcuts") or {}
    for entry in shortcuts.values():
        if not isinstance(entry, dict):
            continue
        app_id = entry.get("appid") or entry.get("AppId")
        name = entry.get("AppName") or entry.get("appname")
        if isinstance(app_id, int) and name:
            names[app_id & 0xFFFFFFFF] = str(name).strip()
    return names


def _steam_vdf_creation_times(steam_root: Path, account_id: str) -> dict[str, int]:
    """``"<appid>/<filename>" -> unix time`` from Steam's screenshots.vdf."""
    path = steam_root / "userdata" / account_id / "760" / "screenshots.vdf"
    try:
        data = vdf_parse(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    out: dict[str, int] = {}
    root = data.get("Screenshots") or data.get("screenshots") or {}
    for apps in root.values():
        if not isinstance(apps, dict):
            continue
        for app_id, entries in apps.items():
            if not isinstance(entries, dict):
                continue
            for entry in entries.values():
                if not isinstance(entry, dict) or not entry.get("filename"):
                    continue
                try:
                    created = int(entry.get("creation", 0))
                except (TypeError, ValueError):
                    continue
                if created:
                    basename = entry["filename"].replace("\\", "/").rsplit("/", 1)[-1]
                    out[f"{app_id}/{basename}"] = created
    return out


def steam_game_ref(app_id: int, installed: dict[int, str], shortcuts: dict[int, str],
                   known_names: dict[str, str]) -> GameRef:
    key = f"steam:{app_id}"
    if app_id >= _SHORTCUT_ID_FLOOR:
        name = shortcuts.get(app_id) or known_names.get(key) or f"Non-Steam game {app_id}"
        return GameRef(key=key, source="steam", name=name)
    return GameRef(key=key, source="steam", name=installed.get(app_id) or known_names.get(key, ""),
                   steam_app_id=app_id)


def scan_steam(steam_root: Path, result: ScanResult, known_names: dict[str, str] | None = None) -> None:
    known_names = known_names or {}
    installed = steam_installed_names(steam_root)
    for account in steam_account_ids(steam_root):
        remote = steam_root / "userdata" / account / "760" / "remote"
        try:
            app_dirs = [e for e in os.scandir(remote) if e.is_dir() and e.name.isdigit()]
        except OSError:
            continue
        shortcuts = steam_shortcut_names(steam_root, account)
        creation = _steam_vdf_creation_times(steam_root, account)
        for app_dir in app_dirs:
            app_id = int(app_dir.name)
            game = steam_game_ref(app_id, installed, shortcuts, known_names)
            try:
                entries = list(os.scandir(os.path.join(app_dir.path, "screenshots")))
            except OSError:
                continue
            for entry in entries:
                if os.path.splitext(entry.name)[1].lower() not in STEAM_EXTENSIONS:
                    continue
                try:
                    if not entry.is_file():
                        continue
                    stat = entry.stat()
                except OSError:
                    continue
                result.add(LocalFile(
                    path=entry.path,
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    game=game,
                    taken_at_hint=capture_date_hint(
                        entry.name, stat.st_mtime, creation.get(f"{app_id}/{entry.name}")
                    ),
                ))


# ── Special K ────────────────────────────────────────────────────────────────


def clean_specialk_folder_name(folder_name: str) -> str:
    """Turn ``EldenRing.exe`` into ``Elden Ring``.

    Mirrors the server's Special K importer so both sources land on the same
    game: strip ``.exe``, split CamelCase and letter/digit boundaries.
    """
    name = folder_name.strip()
    if name.lower().endswith(".exe"):
        name = name[:-4]
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    name = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", name)
    name = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or folder_name


def scan_specialk(root: Path, result: ScanResult) -> None:
    """Every folder under ``root`` is a game; captures anywhere inside it count."""
    try:
        game_dirs = sorted((e for e in os.scandir(root) if e.is_dir()), key=lambda e: e.name.lower())
    except OSError:
        return
    for game_dir in game_dirs:
        game = GameRef(key=f"specialk:{game_dir.name}", source="specialk",
                       name=clean_specialk_folder_name(game_dir.name))
        for dirpath, dirnames, filenames in os.walk(game_dir.path):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for filename in filenames:
                if os.path.splitext(filename)[1].lower() not in SPECIALK_EXTENSIONS:
                    continue
                path = os.path.join(dirpath, filename)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                result.add(LocalFile(
                    path=path,
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    game=game,
                    taken_at_hint=capture_date_hint(filename, stat.st_mtime),
                ))


# ── Everything ───────────────────────────────────────────────────────────────


def resolve_roots(steam_enabled: bool, steam_path: str, specialk_enabled: bool,
                  specialk_path: str) -> tuple[Path | None, Path | None]:
    steam = specialk = None
    if steam_enabled:
        steam = Path(steam_path) if steam_path else find_steam_root()
    if specialk_enabled:
        specialk = Path(specialk_path) if specialk_path else find_specialk_root()
    return steam, specialk


def scan(steam_enabled: bool = True, steam_path: str = "", specialk_enabled: bool = True,
         specialk_path: str = "", known_names: dict[str, str] | None = None) -> ScanResult:
    started = time.monotonic()
    result = ScanResult()
    steam, specialk = resolve_roots(steam_enabled, steam_path, specialk_enabled, specialk_path)
    if steam_enabled:
        if steam is None:
            result.problems.append("Steam wasn't found on this PC.")
        elif not (steam / "userdata").is_dir():
            result.problems.append(f"No Steam screenshots folder in {steam}.")
        else:
            result.steam_root = steam
            scan_steam(steam, result, known_names)
    if specialk_enabled and specialk is not None:
        if specialk.is_dir():
            result.specialk_root = specialk
            scan_specialk(specialk, result)
        elif specialk_path:
            result.problems.append(f"The Special K folder {specialk} doesn't exist.")
    log.debug("scanned %d files in %d games in %.0f ms", len(result.files), len(result.games),
              (time.monotonic() - started) * 1000)
    return result
