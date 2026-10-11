"""Only one copy runs; starting it again brings the running one to the front."""

from __future__ import annotations

import getpass
import logging
import socket
import sys
import threading
import time
import zlib
from collections.abc import Callable

log = logging.getLogger(__name__)

# Per Windows session, so each signed-in user gets their own copy.
_MUTEX_NAME = "Local\\GameVaultSync"
_ERROR_ALREADY_EXISTS = 183


def _default_port() -> int:
    # One port per user account, so two people signed in on one PC don't collide.
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    return 47000 + zlib.crc32(user.encode("utf-8")) % 900


_HELLO = b"gamevault-sync:show\n"


class SingleInstance:
    def __init__(self, port: int | None = None):
        self.port = port or _default_port()
        self._server: socket.socket | None = None
        self._mutex = None
        self._on_show: Callable[[], None] = lambda: None

    def acquire(self) -> bool:
        """True when this is the only copy; otherwise asks the other copy to show itself.

        On Windows a named mutex decides, so two copies started at once (a
        double double-click, or login plus a click) can't both win. The local
        socket only carries the "show yourself" message there; elsewhere it
        decides too.
        """
        if sys.platform == "win32":
            if self._another_copy_holds_the_mutex():
                # It may still be starting up and not listening yet.
                for _ in range(10):
                    if self._poke_other():
                        break
                    time.sleep(0.3)
                return False
            self._listen()
            return True
        if self._listen():
            return True
        if self._poke_other():
            return False
        # Something else owns the port; run anyway rather than refuse to start.
        log.warning("port %d is taken by another program; skipping single-instance check", self.port)
        return True

    def _another_copy_holds_the_mutex(self) -> bool:
        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
            already = ctypes.get_last_error() == _ERROR_ALREADY_EXISTS  # type: ignore[attr-defined]
        except Exception:
            log.warning("couldn't create the single-instance mutex", exc_info=True)
            return False
        if already:
            if handle:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
            return True
        self._mutex = (kernel32, handle)  # held until the app exits
        return False

    def _listen(self) -> bool:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            server.bind(("127.0.0.1", self.port))
            server.listen(4)
        except OSError:
            server.close()
            if sys.platform == "win32":
                log.warning("port %d is taken; a second copy can't bring this one to the front", self.port)
            return False
        self._server = server
        threading.Thread(target=self._serve, args=(server,), name="single-instance", daemon=True).start()
        return True

    def on_show(self, callback: Callable[[], None]) -> None:
        self._on_show = callback

    def _poke_other(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=2) as conn:
                conn.sendall(_HELLO)
                return conn.recv(16).startswith(b"ok")
        except OSError:
            return False

    def _serve(self, server: socket.socket) -> None:
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return  # released
            with conn:
                conn.settimeout(2)
                try:
                    if conn.recv(64) == _HELLO:
                        conn.sendall(b"ok\n")
                        self._on_show()
                        # Let the other copy hang up first, so the leftover
                        # TIME_WAIT is on its side, not on this listening port.
                        conn.recv(1)
                except OSError:
                    pass

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._mutex is not None:
            kernel32, handle = self._mutex
            try:
                import ctypes

                kernel32.CloseHandle(ctypes.c_void_p(handle))
            except Exception:
                pass
            self._mutex = None
