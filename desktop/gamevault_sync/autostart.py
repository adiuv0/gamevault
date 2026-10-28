"""Start with Windows (or with the desktop session on Linux)."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "GameVault Sync"


def launch_command() -> list[str]:
    """How to start this app in the background, frozen exe or Python package."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--background"]
    python = Path(sys.executable)
    if sys.platform == "win32" and python.name.lower() == "python.exe":
        windowless = python.with_name("pythonw.exe")
        if windowless.exists():
            python = windowless
    return [str(python), "-m", "gamevault_sync", "--background"]


def _windows_command() -> str:
    return " ".join(f'"{part}"' if " " in part or not part else part for part in launch_command())


def _desktop_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / "gamevault-sync.desktop"


def supported() -> bool:
    return sys.platform == "win32" or sys.platform.startswith("linux")


def is_enabled() -> bool:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                winreg.QueryValueEx(key, _VALUE_NAME)
            return True
        except OSError:
            return False
    if sys.platform.startswith("linux"):
        return _desktop_file().exists()
    return False


def set_enabled(enabled: bool) -> None:
    if sys.platform == "win32":
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, _windows_command())
            else:
                try:
                    winreg.DeleteValue(key, _VALUE_NAME)
                except FileNotFoundError:
                    pass
        return
    if sys.platform.startswith("linux"):
        path = _desktop_file()
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "[Desktop Entry]\nType=Application\nName=GameVault Sync\n"
                f"Exec={shlex.join(launch_command())}\nX-GNOME-Autostart-enabled=true\n",
                encoding="utf-8",
            )
        else:
            path.unlink(missing_ok=True)


def refresh() -> None:
    """Point an existing entry at this copy of the app (it may have moved or updated)."""
    if is_enabled():
        set_enabled(True)
