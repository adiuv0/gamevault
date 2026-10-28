"""The notification-area icon. Its menu hands commands to the window's queue."""

from __future__ import annotations

import logging
import queue
import threading

from . import APP_NAME
from .icon import app_icon

log = logging.getLogger(__name__)


class Tray:
    def __init__(self, commands: queue.Queue):
        import pystray

        self._paused = False
        self._state = "setup"
        put = commands.put
        menu = pystray.Menu(
            pystray.MenuItem(f"Open {APP_NAME}", lambda: put(("show",)), default=True),
            pystray.MenuItem("Sync now", lambda: put(("sync",))),
            pystray.MenuItem("Pause for 1 hour", lambda: put(("pause", 3600)),
                             visible=lambda item: not self._paused),
            pystray.MenuItem("Resume", lambda: put(("resume",)), visible=lambda item: self._paused),
            pystray.MenuItem("Open GameVault", lambda: put(("web",))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: put(("quit",))),
        )
        self.icon = pystray.Icon("GameVaultSync", app_icon(64, "setup"), APP_NAME, menu)

    def start(self) -> None:
        try:
            self.icon.run_detached()
        except NotImplementedError:
            threading.Thread(target=self.icon.run, name="tray", daemon=True).start()

    @property
    def visible(self) -> bool:
        """False when the desktop has nowhere to show tray icons."""
        return bool(getattr(self.icon, "visible", False))

    def update(self, state: str, tooltip: str) -> None:
        try:
            if state != self._state:
                self._state = state
                self.icon.icon = app_icon(64, state)
            self.icon.title = tooltip[:120]
            paused = state == "paused"
            if paused != self._paused:
                self._paused = paused
                self.icon.update_menu()
        except Exception:
            log.debug("tray update failed", exc_info=True)

    def notify(self, title: str, message: str) -> None:
        if not getattr(self.icon, "HAS_NOTIFICATION", False):
            return
        try:
            self.icon.notify(message, title)
        except Exception:
            log.debug("notification failed", exc_info=True)

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass


def start_tray(commands: queue.Queue) -> Tray | None:
    try:
        tray = Tray(commands)
        tray.start()
        return tray
    except Exception:
        log.warning("no tray icon on this desktop", exc_info=True)
        return None
