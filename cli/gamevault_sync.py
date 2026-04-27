#!/usr/bin/env python3
"""
GameVault Sync — standalone script to sync local screenshots to a GameVault server.

Supports two sources:
  - Steam: walks Steam's userdata/<id>/760/remote/<appid>/screenshots/
  - Special K: walks a user-supplied root where each subfolder is a game
    (handles both .png SDR and .jxr HDR captures)

Single-file tool with an embedded VDF parser and tkinter GUI.
Only external dependency: httpx

Usage:
    python gamevault_sync.py                            # launch GUI
    python gamevault_sync.py --no-gui --mode steam ...  # headless: Steam only
    python gamevault_sync.py --no-gui --mode specialk \
        --specialk-path "C:/Users/You/Documents/My Mods/SpecialK/Profiles" ...
    python gamevault_sync.py --no-gui --mode both ...   # both sources
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Dependency check — httpx is the only external requirement
# ---------------------------------------------------------------------------

try:
    import httpx  # noqa: F401
except ImportError:
    print("ERROR: 'httpx' is not installed.", file=sys.stderr)
    print("Run:  pip install httpx", file=sys.stderr)
    resp = input("\nInstall it now? [y/N] ").strip().lower()
    if resp == "y":
        subprocess.check_call([sys.executable, "-m", "pip", "install", "httpx"])
        import httpx  # noqa: F401
        print()
    else:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Embedded Valve KeyValue (VDF) Parser
# ---------------------------------------------------------------------------

class VDFParseError(Exception):
    pass


def _vdf_tokenize(text: str) -> list[str]:
    """Tokenize a Valve KeyValue string into a list of tokens.

    Tokens are quoted strings (without the quotes), or the braces { }.
    Whitespace is skipped.  ``//`` comments run to end-of-line.
    Backslash escapes inside quoted strings are honoured.
    """
    tokens: list[str] = []
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]

        # Skip whitespace
        if ch in (" ", "\t", "\r", "\n"):
            i += 1
            continue

        # Skip // line comments
        if ch == "/" and i + 1 < length and text[i + 1] == "/":
            i += 2
            while i < length and text[i] != "\n":
                i += 1
            continue

        # Braces
        if ch in ("{", "}"):
            tokens.append(ch)
            i += 1
            continue

        # Quoted string
        if ch == '"':
            i += 1  # skip opening quote
            buf: list[str] = []
            while i < length:
                c = text[i]
                if c == "\\":
                    i += 1
                    if i < length:
                        esc = text[i]
                        if esc == "n":
                            buf.append("\n")
                        elif esc == "t":
                            buf.append("\t")
                        elif esc == "\\":
                            buf.append("\\")
                        elif esc == '"':
                            buf.append('"')
                        else:
                            buf.append(esc)
                        i += 1
                    continue
                if c == '"':
                    i += 1  # skip closing quote
                    break
                buf.append(c)
                i += 1
            tokens.append("".join(buf))
            continue

        # Unquoted token (up to whitespace or special char)
        buf2: list[str] = []
        while i < length and text[i] not in (" ", "\t", "\r", "\n", '"', "{", "}"):
            buf2.append(text[i])
            i += 1
        if buf2:
            tokens.append("".join(buf2))
            continue

        i += 1

    return tokens


def _vdf_parse_tokens(tokens: list[str], pos: int = 0) -> tuple[dict, int]:
    """Parse tokens into a nested dict.  Returns ``(result_dict, next_pos)``."""
    result: dict = {}
    while pos < len(tokens):
        token = tokens[pos]
        if token == "}":
            return result, pos + 1
        key = token
        pos += 1
        if pos >= len(tokens):
            raise VDFParseError(f"Unexpected end after key {key!r}")
        nxt = tokens[pos]
        if nxt == "{":
            pos += 1
            child, pos = _vdf_parse_tokens(tokens, pos)
            result[key] = child
        else:
            result[key] = nxt
            pos += 1
    return result, pos


def vdf_parse(text: str) -> dict:
    """Parse a Valve KeyValue string into a nested dict."""
    tokens = _vdf_tokenize(text)
    result, _ = _vdf_parse_tokens(tokens)
    return result


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class LocalScreenshot:
    path: Path
    source: str  # "steam" or "specialk"
    filename: str
    width: int = 0
    height: int = 0
    creation: int = 0
    sha256: str = ""


@dataclass
class GameScanResult:
    """One scanned game from either source.

    For Steam: ``app_id`` is set, ``folder_name`` is empty.
    For Special K: ``folder_name`` is set (the on-disk folder), ``app_id`` is "".
    ``name`` is filled in from the GameVault server (Steam) or by cleaning the
    folder name (Special K) before display + upload.
    """

    source: str
    app_id: str = ""
    folder_name: str = ""
    name: str = ""
    screenshots: list[LocalScreenshot] = field(default_factory=list)
    new_hashes: set[str] = field(default_factory=set)

    @property
    def key(self) -> str:
        """Unique cross-source identifier for use as a dict key."""
        if self.source == "steam":
            return f"steam:{self.app_id}"
        return f"specialk:{self.folder_name}"

    @property
    def new_count(self) -> int:
        return sum(1 for s in self.screenshots if s.sha256 in self.new_hashes)

    @property
    def total_count(self) -> int:
        return len(self.screenshots)


# ---------------------------------------------------------------------------
# Steam scanner functions
# ---------------------------------------------------------------------------

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tga"}

# Special K writes both .png (SDR) and .jxr (HDR). The GameVault upload
# pipeline accepts both — JXR is tone-mapped server-side for thumbnails.
SPECIALK_EXTENSIONS = {".jxr", ".png"}


def find_steam_path() -> Optional[Path]:
    """Check common Windows paths for a Steam installation."""
    candidates = [
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"C:\Program Files\Steam"),
        Path(r"D:\Steam"),
        Path(r"D:\SteamLibrary\Steam"),
        Path(os.path.expandvars(r"%LOCALAPPDATA%\Steam")),
    ]
    for p in candidates:
        if (p / "steam.exe").exists() or (p / "userdata").is_dir():
            return p
    return None


def find_user_ids(steam_path: Path) -> list[str]:
    """List numeric directory names inside ``userdata/``."""
    userdata = steam_path / "userdata"
    if not userdata.is_dir():
        return []
    return [d.name for d in userdata.iterdir() if d.is_dir() and d.name.isdigit()]


def _load_vdf_metadata(steam_path: Path, user_id: str) -> dict[str, dict]:
    """Load ``screenshots.vdf`` and return a mapping of ``appid/filename`` to metadata."""
    vdf_path = steam_path / "userdata" / user_id / "760" / "screenshots.vdf"
    meta: dict[str, dict] = {}
    if not vdf_path.exists():
        return meta
    try:
        text = vdf_path.read_text(encoding="utf-8", errors="replace")
        data = vdf_parse(text)
    except Exception:
        return meta

    # Navigate into the VDF structure.
    # Typical shape: "Screenshots" -> <userid> -> <appid> -> <index> -> {filename, width, ...}
    screenshots_root = data.get("Screenshots") or data.get("screenshots") or {}
    for uid_key, uid_val in screenshots_root.items():
        if not isinstance(uid_val, dict):
            continue
        for app_id_key, app_val in uid_val.items():
            if not isinstance(app_val, dict):
                continue
            for entry_key, entry_val in app_val.items():
                if not isinstance(entry_val, dict):
                    continue
                fname = entry_val.get("filename", "")
                if fname:
                    basename = Path(fname).name
                    lookup_key = f"{app_id_key}/{basename}"
                    meta[lookup_key] = entry_val
    return meta


def scan_local_screenshots(steam_path: Path, user_id: str) -> dict[str, GameScanResult]:
    """Scan ``remote/<appid>/screenshots/`` for image files, cross-ref with VDF metadata."""
    remote_dir = steam_path / "userdata" / user_id / "760" / "remote"
    results: dict[str, GameScanResult] = {}

    if not remote_dir.is_dir():
        return results

    vdf_meta = _load_vdf_metadata(steam_path, user_id)

    for app_dir in remote_dir.iterdir():
        if not app_dir.is_dir() or not app_dir.name.isdigit():
            continue
        app_id = app_dir.name
        ss_dir = app_dir / "screenshots"
        if not ss_dir.is_dir():
            continue

        game = GameScanResult(source="steam", app_id=app_id)
        for img_file in ss_dir.iterdir():
            if not img_file.is_file():
                continue
            if img_file.suffix.lower() not in IMAGE_EXTENSIONS:
                continue

            ls = LocalScreenshot(
                path=img_file,
                source="steam",
                filename=img_file.name,
            )

            # Try to pull VDF metadata
            lookup = f"{app_id}/{img_file.name}"
            if lookup in vdf_meta:
                m = vdf_meta[lookup]
                try:
                    ls.width = int(m.get("width", 0))
                except (ValueError, TypeError):
                    pass
                try:
                    ls.height = int(m.get("height", 0))
                except (ValueError, TypeError):
                    pass
                try:
                    ls.creation = int(m.get("creation", 0))
                except (ValueError, TypeError):
                    pass

            game.screenshots.append(ls)

        if game.screenshots:
            results[app_id] = game

    return results


# ---------------------------------------------------------------------------
# Special K scanner
# ---------------------------------------------------------------------------


def clean_specialk_folder_name(folder_name: str) -> str:
    """Mirror of the server-side cleaner in specialk_import_service.

    Strip ``.exe``, split CamelCase, split letter→digit boundaries.
    Mirrors backend behaviour so client- and server-resolved game names
    converge on the same string.
    """
    name = folder_name.strip()
    if name.lower().endswith(".exe"):
        name = name[:-4]
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    name = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", name)
    name = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or folder_name


def scan_specialk_path(root: Path) -> dict[str, GameScanResult]:
    """Scan a Special K root directory for per-game subfolders.

    Returns a dict keyed by ``GameScanResult.key`` (``specialk:<folder>``).
    Each top-level subdirectory becomes one game; screenshots are collected
    recursively under it (so HDR/SDR subfolder splits are handled).
    """
    results: dict[str, GameScanResult] = {}
    if not root.exists() or not root.is_dir():
        return results

    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue

        game = GameScanResult(
            source="specialk",
            folder_name=child.name,
            name=clean_specialk_folder_name(child.name),
        )

        for path in child.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SPECIALK_EXTENSIONS:
                continue

            game.screenshots.append(
                LocalScreenshot(
                    path=path,
                    source="specialk",
                    filename=path.name,
                )
            )

        if game.screenshots:
            results[game.key] = game

    return results


def compute_hashes(
    screenshots: list[LocalScreenshot],
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Compute SHA-256 hash for each screenshot in-place."""
    total = len(screenshots)
    for i, ss in enumerate(screenshots):
        h = hashlib.sha256()
        with open(ss.path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        ss.sha256 = h.hexdigest()
        if progress_cb:
            progress_cb(i + 1, total)


# ---------------------------------------------------------------------------
# GameVault API client
# ---------------------------------------------------------------------------

class GameVaultClient:
    def __init__(self, server: str, token: str):
        self.client = httpx.Client(
            base_url=server.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=60.0,
        )

    def check_hashes(self, hashes: list[str]) -> dict:
        resp = self.client.post(
            "/api/screenshots/check-hashes", json={"hashes": hashes}
        )
        resp.raise_for_status()
        return resp.json()

    def get_or_create_game(self, app_id: str) -> dict:
        resp = self.client.get(f"/api/games/by-steam-appid/{app_id}")
        resp.raise_for_status()
        return resp.json()

    def get_or_create_game_by_name(self, name: str) -> dict:
        """Resolve a game by display name, creating it if missing.

        Used for Special K imports where there's no Steam app ID — the
        cleaned folder name becomes the canonical game name. Existing games
        with a matching name (e.g. created by an earlier Steam import) are
        returned as-is, so the two sources merge into one library entry.
        """
        resp = self.client.post("/api/games/by-name", json={"name": name})
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _guess_mime(filename: str) -> str:
        ext = filename.lower().rsplit(".", 1)[-1]
        return {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "webp": "image/webp",
            "bmp": "image/bmp",
            "tiff": "image/tiff",
            "gif": "image/gif",
            "jxr": "image/vnd.ms-photo",
        }.get(ext, "application/octet-stream")

    def upload_screenshot(self, game_id: int, file_path: Path, filename: str) -> dict:
        """Upload via the synchronous endpoint so the file is fully processed
        before the response returns (no background task race condition)."""
        mime = self._guess_mime(filename)
        with open(file_path, "rb") as f:
            resp = self.client.post(
                "/api/upload/sync",
                data={"game_id": str(game_id)},
                files={"files": (filename, f, mime)},
                timeout=300.0,  # JXR HDR files are large + tone-mapped server-side
            )
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self.client.close()


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------

CONFIG_PATH = Path.home() / ".gamevault_sync.json"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI mode
# ---------------------------------------------------------------------------

def _resolve_steam_path(arg_path: Optional[str]) -> Optional[Path]:
    """Resolve the Steam install root, either from --steam-path or auto-detect."""
    if arg_path:
        return Path(arg_path)
    return find_steam_path()


def _scan_steam_into(
    steam_path: Path, all_games: dict[str, GameScanResult]
) -> int:
    """Scan all Steam user IDs into ``all_games`` keyed by ``GameScanResult.key``.

    Returns the number of screenshots discovered.
    """
    user_ids = find_user_ids(steam_path)
    if not user_ids:
        return 0

    discovered = 0
    for uid in user_ids:
        games = scan_local_screenshots(steam_path, uid)
        for game in games.values():
            existing = all_games.get(game.key)
            if existing is not None:
                existing.screenshots.extend(game.screenshots)
            else:
                all_games[game.key] = game
            discovered += len(game.screenshots)
    return discovered


def _scan_specialk_into(
    specialk_path: Path, all_games: dict[str, GameScanResult]
) -> int:
    """Scan a Special K root into ``all_games``. Returns screenshot count."""
    games = scan_specialk_path(specialk_path)
    discovered = 0
    for game in games.values():
        all_games[game.key] = game
        discovered += len(game.screenshots)
    return discovered


def _resolve_game_for_upload(
    client: GameVaultClient,
    game: GameScanResult,
    cache: dict[str, dict],
) -> dict:
    """Get-or-create a GameVault game record for a scanned game.

    Steam: looks up by Steam app ID. Special K: looks up by display name.
    Caches results so we only ask the server once per game.
    """
    if game.key in cache:
        return cache[game.key]

    if game.source == "steam":
        gv_game = client.get_or_create_game(game.app_id)
    else:
        gv_game = client.get_or_create_game_by_name(game.name)

    cache[game.key] = gv_game
    return gv_game


def _sort_games(all_games: dict[str, GameScanResult]) -> list[GameScanResult]:
    """Stable sort: Steam first (by app_id numeric), then Special K (by name)."""
    steam = sorted(
        (g for g in all_games.values() if g.source == "steam"),
        key=lambda g: int(g.app_id) if g.app_id.isdigit() else 0,
    )
    specialk = sorted(
        (g for g in all_games.values() if g.source == "specialk"),
        key=lambda g: g.name.lower(),
    )
    return steam + specialk


def run_cli(args: argparse.Namespace) -> None:
    """Execute the headless CLI workflow."""
    server = args.server
    token = args.token
    dry_run = args.dry_run
    mode = args.mode

    do_steam = mode in ("steam", "both")
    do_specialk = mode in ("specialk", "both")

    all_games: dict[str, GameScanResult] = {}

    # ── Steam scan ────────────────────────────────────────────────────────
    if do_steam:
        steam_path = _resolve_steam_path(args.steam_path)
        if steam_path is None:
            print(
                "ERROR: Could not auto-detect Steam path. Use --steam-path "
                "or --mode specialk to skip Steam.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"Steam path: {steam_path}")
        count = _scan_steam_into(steam_path, all_games)
        print(f"Steam: found {count} screenshots")

    # ── Special K scan ────────────────────────────────────────────────────
    if do_specialk:
        if not args.specialk_path:
            print(
                "ERROR: --specialk-path is required when --mode is specialk or both.",
                file=sys.stderr,
            )
            sys.exit(1)
        sk_path = Path(args.specialk_path)
        if not sk_path.is_dir():
            print(
                f"ERROR: Special K path is not a directory: {sk_path}",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"Special K path: {sk_path}")
        count = _scan_specialk_into(sk_path, all_games)
        print(f"Special K: found {count} screenshots")

    all_screenshots = [ss for g in all_games.values() for ss in g.screenshots]
    print(
        f"Total: {len(all_screenshots)} screenshots across {len(all_games)} games"
    )

    if not all_screenshots:
        print("Nothing to do.")
        return

    # ── Hash ──────────────────────────────────────────────────────────────
    def cli_hash_progress(current: int, total: int) -> None:
        pct = current * 100 // total
        print(f"\rHashing: {current}/{total} ({pct}%)", end="", flush=True)

    compute_hashes(all_screenshots, cli_hash_progress)
    print()

    client = GameVaultClient(server, token)
    try:
        # ── Batch-check hashes ───────────────────────────────────────────
        all_hashes = [ss.sha256 for ss in all_screenshots]
        existing: set[str] = set()
        chunk_size = 500
        for i in range(0, len(all_hashes), chunk_size):
            chunk = all_hashes[i : i + chunk_size]
            result = client.check_hashes(chunk)
            existing.update(result.get("existing", []))
            checked = min(i + chunk_size, len(all_hashes))
            print(
                f"\rChecked {checked}/{len(all_hashes)} hashes against server",
                end="",
                flush=True,
            )
        print()

        # Mark new hashes per game
        for game in all_games.values():
            game_hashes = {ss.sha256 for ss in game.screenshots}
            game.new_hashes = game_hashes - existing

        # ── Resolve game names ───────────────────────────────────────────
        # Steam path hits Steam Store API server-side, so rate-limit to be
        # polite. Special K resolution is local (just a DB read), no
        # throttle needed.
        print("\nResolving game names...")
        gv_game_cache: dict[str, dict] = {}
        ordered = _sort_games(all_games)
        for gi, game in enumerate(ordered):
            try:
                gv_game = _resolve_game_for_upload(client, game, gv_game_cache)
                resolved_name = gv_game.get("name") or game.name
                game.name = resolved_name
                print(
                    f"\r  [{gi + 1}/{len(ordered)}] {resolved_name}",
                    end="",
                    flush=True,
                )
            except Exception as exc:
                if not game.name:
                    game.name = (
                        f"App {game.app_id}" if game.source == "steam"
                        else game.folder_name
                    )
                print(
                    f"\r  [{gi + 1}/{len(ordered)}] {game.name} (failed: {exc})",
                    end="",
                    flush=True,
                )
            if gi < len(ordered) - 1 and game.source == "steam":
                time.sleep(0.3)
        print()

        # ── Per-game summary ──────────────────────────────────────────────
        print("\n--- Per-game summary ---")
        for game in ordered:
            tag = "Steam" if game.source == "steam" else "SpecialK"
            label = game.name or game.folder_name or game.app_id
            print(
                f"  [{tag:>9s}] {label:>40s}    "
                f"{game.new_count} new / {game.total_count} total"
            )

        total_new = sum(g.new_count for g in all_games.values())
        print(f"\nTotal new: {total_new}")

        if dry_run:
            print("Dry run -- skipping upload.")
            return

        if total_new == 0:
            print("Everything is already synced.")
            return

        # ── Upload ────────────────────────────────────────────────────────
        uploaded = 0
        failed = 0
        for game in ordered:
            new_shots = [
                ss for ss in game.screenshots if ss.sha256 in game.new_hashes
            ]
            if not new_shots:
                continue

            try:
                gv_game = _resolve_game_for_upload(
                    client, game, gv_game_cache
                )
            except Exception as exc:
                print(
                    f"\n  ERROR: Could not resolve game {game.name or game.key}: {exc}",
                    file=sys.stderr,
                )
                failed += len(new_shots)
                continue

            gv_game_id = gv_game["id"]
            game_name = gv_game.get("name", game.name)

            for ss in new_shots:
                try:
                    result = client.upload_screenshot(
                        gv_game_id, ss.path, ss.filename
                    )
                    if result.get("uploaded", 0) > 0:
                        uploaded += 1
                        print(
                            f"\r  [{uploaded}/{total_new}] {game_name} — {ss.filename}",
                            end="",
                            flush=True,
                        )
                    else:
                        failed += 1
                        print(
                            f"\n  SKIP: {ss.filename}: server returned uploaded=0 (response: {result})",
                            file=sys.stderr,
                        )
                except Exception as exc:
                    failed += 1
                    print(
                        f"\n  FAIL: {ss.filename}: {exc}",
                        file=sys.stderr,
                    )

        print(f"\n\nDone. Uploaded: {uploaded}, Failed: {failed}")
    finally:
        client.close()


# ---------------------------------------------------------------------------
# tkinter GUI
# ---------------------------------------------------------------------------

def run_gui() -> None:
    """Launch the tkinter GUI."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    cfg = load_config()

    root = tk.Tk()
    root.title("GameVault Sync")
    root.geometry("680x620")
    root.resizable(True, True)

    # ---- Connection frame ----
    conn_frame = ttk.LabelFrame(root, text="Connection", padding=8)
    conn_frame.pack(fill="x", padx=10, pady=(10, 4))

    ttk.Label(conn_frame, text="Server URL:").grid(row=0, column=0, sticky="w")
    server_var = tk.StringVar(value=cfg.get("server", ""))
    ttk.Entry(conn_frame, textvariable=server_var, width=55).grid(
        row=0, column=1, columnspan=2, sticky="ew", padx=(4, 0)
    )

    ttk.Label(conn_frame, text="Auth Token:").grid(row=1, column=0, sticky="w")
    token_var = tk.StringVar(value=cfg.get("token", ""))
    ttk.Entry(conn_frame, textvariable=token_var, width=55, show="*").grid(
        row=1, column=1, columnspan=2, sticky="ew", padx=(4, 0)
    )

    conn_frame.columnconfigure(1, weight=1)

    # ---- Sources frame ----
    src_frame = ttk.LabelFrame(root, text="Sources", padding=8)
    src_frame.pack(fill="x", padx=10, pady=(4, 4))

    ttk.Label(src_frame, text="Mode:").grid(row=0, column=0, sticky="w")
    _MODE_LABELS = {
        "steam": "Steam Only",
        "specialk": "Special K Only",
        "both": "Steam + Special K",
    }
    _MODE_VALUES = list(_MODE_LABELS.values())
    saved_mode = cfg.get("mode", "steam")
    mode_var = tk.StringVar(value=_MODE_LABELS.get(saved_mode, _MODE_LABELS["steam"]))
    mode_combo = ttk.Combobox(
        src_frame,
        textvariable=mode_var,
        values=_MODE_VALUES,
        state="readonly",
        width=22,
    )
    mode_combo.grid(row=0, column=1, sticky="w", padx=(4, 0), pady=(0, 4))

    def _selected_mode() -> str:
        label = mode_var.get()
        for k, v in _MODE_LABELS.items():
            if v == label:
                return k
        return "steam"

    ttk.Label(src_frame, text="Steam Path:").grid(row=1, column=0, sticky="w")
    steam_var = tk.StringVar(value=cfg.get("steam_path", ""))
    ttk.Entry(src_frame, textvariable=steam_var, width=42).grid(
        row=1, column=1, sticky="ew", padx=(4, 0)
    )

    def browse_steam():
        d = filedialog.askdirectory(title="Select Steam installation directory")
        if d:
            steam_var.set(d)

    ttk.Button(src_frame, text="Browse", command=browse_steam).grid(
        row=1, column=2, padx=(4, 0)
    )

    ttk.Label(src_frame, text="Special K Path:").grid(row=2, column=0, sticky="w")
    sk_var = tk.StringVar(value=cfg.get("specialk_path", ""))
    ttk.Entry(src_frame, textvariable=sk_var, width=42).grid(
        row=2, column=1, sticky="ew", padx=(4, 0), pady=(4, 0)
    )

    def browse_sk():
        d = filedialog.askdirectory(title="Select Special K screenshots root")
        if d:
            sk_var.set(d)

    ttk.Button(src_frame, text="Browse", command=browse_sk).grid(
        row=2, column=2, padx=(4, 0), pady=(4, 0)
    )

    src_frame.columnconfigure(1, weight=1)

    # Enable/disable path fields based on mode
    def _refresh_path_state(*_args) -> None:
        mode = _selected_mode()
        steam_state = "normal" if mode in ("steam", "both") else "disabled"
        sk_state = "normal" if mode in ("specialk", "both") else "disabled"
        # Toggle the entry widgets (column 1) and browse buttons (column 2)
        for child in src_frame.grid_slaves(row=1):
            try:
                child.configure(state=steam_state)
            except tk.TclError:
                pass
        for child in src_frame.grid_slaves(row=2):
            try:
                child.configure(state=sk_state)
            except tk.TclError:
                pass

    mode_combo.bind("<<ComboboxSelected>>", _refresh_path_state)
    _refresh_path_state()

    # ---- Scan button ----
    scan_btn = ttk.Button(root, text="Scan")
    scan_btn.pack(pady=6)

    # ---- Games frame with scrollable checkboxes ----
    games_frame = ttk.LabelFrame(root, text="Games", padding=8)
    games_frame.pack(fill="both", expand=True, padx=10, pady=4)

    canvas = tk.Canvas(games_frame, highlightthickness=0)
    scrollbar = ttk.Scrollbar(games_frame, orient="vertical", command=canvas.yview)
    inner_frame = ttk.Frame(canvas)

    inner_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
    )
    canvas.create_window((0, 0), window=inner_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    # ---- Buttons row ----
    btn_row = ttk.Frame(root)
    btn_row.pack(fill="x", padx=10, pady=4)

    select_all_btn = ttk.Button(btn_row, text="Select All New")
    select_all_btn.pack(side="left", padx=(0, 4))
    deselect_btn = ttk.Button(btn_row, text="Deselect All")
    deselect_btn.pack(side="left", padx=(0, 4))
    sync_btn = ttk.Button(btn_row, text="Sync Selected")
    sync_btn.pack(side="right")

    # ---- Progress ----
    progress_var = tk.DoubleVar(value=0.0)
    progress_bar = ttk.Progressbar(root, variable=progress_var, maximum=100)
    progress_bar.pack(fill="x", padx=10, pady=(4, 2))
    status_var = tk.StringVar(value="Ready")
    ttk.Label(root, textvariable=status_var, anchor="w").pack(
        fill="x", padx=10, pady=(0, 10)
    )

    # State
    # game_checks pairs each checkbox variable with the GameScanResult.key
    # so we can look up the underlying game in ``all_games`` after the scan.
    game_checks: list[tuple[tk.BooleanVar, str]] = []
    all_games: dict[str, GameScanResult] = {}

    def clear_inner():
        for w in inner_frame.winfo_children():
            w.destroy()
        game_checks.clear()

    # ---- Scan logic (background thread) ----
    def do_scan():
        mode = _selected_mode()
        do_steam = mode in ("steam", "both")
        do_specialk = mode in ("specialk", "both")

        server = server_var.get().strip()
        token = token_var.get().strip()
        if not server or not token:
            messagebox.showerror(
                "Error", "Server URL and Auth Token are required."
            )
            return

        steam_path_str = steam_var.get().strip()
        sk_path_str = sk_var.get().strip()

        # Resolve / validate paths up front
        sp: Optional[Path] = None
        if do_steam:
            if steam_path_str:
                sp = Path(steam_path_str)
            else:
                sp = find_steam_path()
                if sp:
                    steam_var.set(str(sp))
                else:
                    messagebox.showerror(
                        "Error",
                        "Could not detect Steam path. Browse to it or "
                        "switch the mode to Special K Only.",
                    )
                    return

        sk: Optional[Path] = None
        if do_specialk:
            if not sk_path_str:
                messagebox.showerror(
                    "Error",
                    "Special K Path is required when scanning Special K.",
                )
                return
            sk = Path(sk_path_str)
            if not sk.is_dir():
                messagebox.showerror(
                    "Error",
                    f"Special K Path is not a directory:\n{sk}",
                )
                return

        # Save config (including the new fields)
        save_config({
            "server": server,
            "token": token,
            "steam_path": steam_path_str,
            "specialk_path": sk_path_str,
            "mode": mode,
        })

        scan_btn.configure(state="disabled")
        sync_btn.configure(state="disabled")
        status_var.set("Scanning local screenshots...")
        progress_var.set(0)
        clear_inner()
        all_games.clear()

        def background():
            try:
                if do_steam and sp is not None:
                    user_ids = find_user_ids(sp)
                    if not user_ids and not do_specialk:
                        root.after(
                            0,
                            lambda: messagebox.showerror(
                                "Error", "No Steam user IDs found."
                            ),
                        )
                        return
                    _scan_steam_into(sp, all_games)

                if do_specialk and sk is not None:
                    _scan_specialk_into(sk, all_games)

                all_ss = [
                    ss for g in all_games.values() for ss in g.screenshots
                ]
                total_ss = len(all_ss)
                root.after(
                    0,
                    lambda: status_var.set(
                        f"Hashing {total_ss} screenshots..."
                    ),
                )

                if total_ss == 0:
                    root.after(
                        0, lambda: status_var.set("No screenshots found.")
                    )
                    return

                # Hash (0-50%)
                def hash_progress(cur, tot):
                    pct = cur / tot * 50
                    root.after(0, lambda p=pct: progress_var.set(p))

                compute_hashes(all_ss, hash_progress)

                # Check hashes (50-80%)
                root.after(
                    0,
                    lambda: status_var.set(
                        "Checking hashes against server..."
                    ),
                )
                client = GameVaultClient(server, token)
                try:
                    all_hashes = [ss.sha256 for ss in all_ss]
                    existing: set[str] = set()
                    chunk_size = 500
                    num_chunks = max(
                        1, (len(all_hashes) + chunk_size - 1) // chunk_size
                    )
                    for ci in range(num_chunks):
                        chunk = all_hashes[
                            ci * chunk_size : (ci + 1) * chunk_size
                        ]
                        result = client.check_hashes(chunk)
                        existing.update(result.get("existing", []))
                        pct = 50 + (ci + 1) / num_chunks * 30
                        root.after(0, lambda p=pct: progress_var.set(p))

                    # Resolve game names (80-100%) — Steam goes via Steam
                    # Store API server-side, so rate-limit. Special K hits
                    # only the local DB.
                    root.after(
                        0, lambda: status_var.set("Resolving game names...")
                    )
                    ordered = _sort_games(all_games)
                    cache: dict[str, dict] = {}
                    for gi, game in enumerate(ordered):
                        try:
                            gv_game = _resolve_game_for_upload(
                                client, game, cache
                            )
                            game.name = gv_game.get("name") or game.name
                        except Exception:
                            if not game.name:
                                game.name = (
                                    f"App {game.app_id}"
                                    if game.source == "steam"
                                    else game.folder_name
                                )
                        pct = 80 + (gi + 1) / max(1, len(ordered)) * 20
                        _name = game.name
                        root.after(
                            0,
                            lambda p=pct, n=_name, c=gi + 1, t=len(ordered): (
                                progress_var.set(p),
                                status_var.set(
                                    f"Resolving names... {c}/{t} — {n}"
                                ),
                            ),
                        )
                        if gi < len(ordered) - 1 and game.source == "steam":
                            time.sleep(0.3)
                finally:
                    client.close()

                for game in all_games.values():
                    game_hashes = {ss.sha256 for ss in game.screenshots}
                    game.new_hashes = game_hashes - existing

                def populate():
                    clear_inner()
                    for game in _sort_games(all_games):
                        var = tk.BooleanVar(value=game.new_count > 0)
                        game_checks.append((var, game.key))
                        tag = "Steam" if game.source == "steam" else "SpecialK"
                        display_name = game.name or game.folder_name or game.app_id
                        label = (
                            f"[{tag}] {display_name}    "
                            f"{game.new_count} new / {game.total_count} total"
                        )
                        cb = ttk.Checkbutton(
                            inner_frame, text=label, variable=var
                        )
                        cb.pack(anchor="w", pady=1)

                    total_new = sum(
                        g.new_count for g in all_games.values()
                    )
                    status_var.set(
                        f"Scan complete. {total_new} new screenshots found."
                    )
                    progress_var.set(100)

                root.after(0, populate)

            except Exception as exc:
                root.after(
                    0,
                    lambda: messagebox.showerror("Scan Error", str(exc)),
                )
                root.after(0, lambda: status_var.set("Scan failed."))
            finally:
                root.after(
                    0, lambda: scan_btn.configure(state="normal")
                )
                root.after(
                    0, lambda: sync_btn.configure(state="normal")
                )

        threading.Thread(target=background, daemon=True).start()

    # ---- Select / Deselect helpers ----
    def do_select_all_new():
        for var, key in game_checks:
            game = all_games.get(key)
            if game and game.new_count > 0:
                var.set(True)

    def do_deselect_all():
        for var, _ in game_checks:
            var.set(False)

    # ---- Sync logic (background thread) ----
    def do_sync():
        server = server_var.get().strip()
        token = token_var.get().strip()
        if not server or not token:
            messagebox.showerror(
                "Error", "Server URL and Auth Token are required."
            )
            return

        selected_keys = [key for var, key in game_checks if var.get()]
        if not selected_keys:
            messagebox.showinfo("Info", "No games selected.")
            return

        # Gather new screenshots for selected games
        to_upload: list[tuple[str, LocalScreenshot]] = []
        for key in selected_keys:
            game = all_games.get(key)
            if not game:
                continue
            for ss in game.screenshots:
                if ss.sha256 in game.new_hashes:
                    to_upload.append((key, ss))

        if not to_upload:
            messagebox.showinfo("Info", "No new screenshots to upload.")
            return

        scan_btn.configure(state="disabled")
        sync_btn.configure(state="disabled")
        progress_var.set(0)
        status_var.set(f"Uploading {len(to_upload)} screenshots...")

        def background():
            uploaded = 0
            failed = 0
            client = GameVaultClient(server, token)
            try:
                gv_game_cache: dict[str, dict] = {}
                total = len(to_upload)

                last_error = ""
                for i, (key, ss) in enumerate(to_upload):
                    try:
                        game = all_games[key]
                        gv_game = _resolve_game_for_upload(
                            client, game, gv_game_cache
                        )
                        result = client.upload_screenshot(
                            gv_game["id"], ss.path, ss.filename
                        )
                        if result.get("uploaded", 0) > 0:
                            uploaded += 1
                        else:
                            failed += 1
                            last_error = f"Server skipped: {result}"
                            print(
                                f"Server skipped ({ss.filename}): {result}",
                                file=sys.stderr,
                            )
                    except Exception as exc:
                        failed += 1
                        last_error = str(exc)
                        print(
                            f"Upload error ({ss.filename}): {exc}",
                            file=sys.stderr,
                        )

                    pct = (i + 1) / total * 100
                    _uploaded = uploaded
                    _failed = failed
                    _fname = ss.filename
                    root.after(
                        0,
                        lambda p=pct, u=_uploaded, f=_failed, t=total, fn=_fname: (
                            progress_var.set(p),
                            status_var.set(
                                f"Uploaded {u}/{t} (failed: {f}) — {fn}"
                            ),
                        ),
                    )

                def show_done():
                    msg = (
                        f"Upload complete.\n\n"
                        f"Uploaded: {uploaded}\n"
                        f"Failed: {failed}"
                    )
                    if failed > 0 and last_error:
                        msg += f"\n\nLast error:\n{last_error}"
                    messagebox.showinfo("Sync Complete", msg)
                    status_var.set(
                        f"Done. {uploaded} uploaded, {failed} failed."
                    )
                    progress_var.set(100)

                root.after(0, show_done)
            except Exception as exc:
                root.after(
                    0,
                    lambda: messagebox.showerror("Sync Error", str(exc)),
                )
                root.after(0, lambda: status_var.set("Sync failed."))
            finally:
                client.close()
                root.after(
                    0, lambda: scan_btn.configure(state="normal")
                )
                root.after(
                    0, lambda: sync_btn.configure(state="normal")
                )

        threading.Thread(target=background, daemon=True).start()

    # Wire up buttons
    scan_btn.configure(command=do_scan)
    select_all_btn.configure(command=do_select_all_new)
    deselect_btn.configure(command=do_deselect_all)
    sync_btn.configure(command=do_sync)

    root.mainloop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "GameVault Sync -- sync local Steam and Special K screenshots "
            "to a GameVault server."
        )
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="Run in CLI mode (no tkinter window).",
    )
    parser.add_argument(
        "--server",
        type=str,
        default=None,
        help="GameVault server URL (required for --no-gui).",
    )
    parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="Auth token / JWT for the GameVault API (required for --no-gui).",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=("steam", "specialk", "both"),
        default="steam",
        help=(
            "Which sources to sync: steam (default), specialk, or both. "
            "specialk requires --specialk-path."
        ),
    )
    parser.add_argument(
        "--steam-path",
        type=str,
        default=None,
        help="Path to Steam installation directory (auto-detected if omitted).",
    )
    parser.add_argument(
        "--specialk-path",
        type=str,
        default=None,
        help=(
            "Root of the Special K screenshots tree — each top-level "
            "subfolder is treated as a game. Required when --mode is "
            "specialk or both."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report only -- do not upload.",
    )

    args = parser.parse_args()

    if args.no_gui:
        if not args.server or not args.token:
            parser.error("--server and --token are required with --no-gui")
        if args.mode in ("specialk", "both") and not args.specialk_path:
            parser.error(
                "--specialk-path is required when --mode is specialk or both"
            )
        run_cli(args)
    else:
        run_gui()


if __name__ == "__main__":
    main()
