"""Where the app keeps its settings, sync memory and logs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The single-file script stored everything here; read once to migrate.
LEGACY_CONFIG_PATH = Path.home() / ".gamevault_sync.json"


def data_dir(create: bool = True) -> Path:
    override = os.environ.get("GAMEVAULT_SYNC_HOME")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        path = Path(base) / "GameVault Sync"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "GameVault Sync"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        path = Path(base) / "gamevault-sync"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def log_path() -> Path:
    return data_dir() / "gamevault-sync.log"


def state_db_path(server_key: str) -> Path:
    """The sync memory for one server (see ``Settings.server_key``).

    One file per server, so switching servers, or a one-off ``--sync-once``
    against another one, never makes the app forget what it sent where.
    """
    return data_dir() / f"sync-state-{server_key}.db"
