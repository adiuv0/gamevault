"""The background loop that keeps syncing while the app runs."""

from __future__ import annotations

import copy
import logging
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from . import api, app_paths
from .api import (
    ApiError,
    GameVaultClient,
    ServerUnreachableError,
    SetupRequiredError,
    SignedOutError,
)
from .config import Settings
from .engine import Progress, SyncEngine, SyncReport
from .state import SyncState

log = logging.getLogger(__name__)

# How long to wait before trying an unreachable server again.
_OFFLINE_BACKOFF = (30, 60, 120, 300, 600)


@dataclass
class Status:
    state: str = "starting"  # setup | signed_out | ready | syncing | idle | paused | offline | error
    message: str = ""
    progress: Progress | None = None
    last_report: SyncReport | None = None
    last_check_at: float | None = None
    next_check_at: float | None = None
    paused_until: float | None = None  # 0 means until resumed
    synced_total: int = 0
    version: int = 0  # bumps on every change, so a window can skip redraws


def device_name(settings: Settings) -> str:
    return settings.device_name or socket.gethostname() or "Gaming PC"


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%I:%M %p").lstrip("0")


def ago(ts: float | None, now: float | None = None) -> str:
    if not ts:
        return "never"
    seconds = max(0, (now or time.time()) - ts)
    if seconds < 45:
        return "just now"
    if seconds < 3600:
        return f"{round(seconds / 60)} min ago"
    if seconds < 86400:
        return f"{round(seconds / 3600)} h ago"
    return datetime.fromtimestamp(ts).strftime("%b %d")


def describe(status: Status) -> tuple[str, str]:
    """A headline and a detail line for the window and the tray tooltip."""
    report = status.last_report
    if status.state == "setup":
        return "Not signed in", "Sign in to start backing up your screenshots."
    if status.state == "signed_out":
        return "Signed out", status.message or "Sign in again to keep syncing."
    if status.state == "starting":
        return "Starting…", ""
    if status.state == "syncing":
        progress = status.progress or Progress("scanning")
        if progress.phase == "scanning":
            return "Looking for new screenshots…", ""
        if progress.phase == "reading":
            return "Reading new screenshots…", f"{progress.done:,} of {progress.total:,}"
        if progress.phase == "checking":
            return "Checking with GameVault…", ""
        if progress.total:
            return f"Uploading {min(progress.done + 1, progress.total):,} of {progress.total:,}", progress.current
        return "Uploading…", progress.current
    if status.state == "ready":
        new = report.new if report else 0
        games = sum(1 for g in report.games if g.new and not g.excluded) if report else 0
        if not new:
            return "Ready", "Everything here is already in GameVault."
        return (f"Ready to upload {_plural(new, 'screenshot')}",
                f"From {_plural(games, 'game')}. Nothing is uploaded until you start.")
    if status.state == "paused":
        until = status.paused_until
        return "Paused", f"Until {_clock(until)}" if until else "Until you resume"
    if status.state == "offline":
        wait = f" Trying again {('at ' + _clock(status.next_check_at)) if status.next_check_at else 'soon'}."
        return "Can't reach GameVault", (status.message or "The server isn't answering.") + wait
    if status.state == "error":
        return "Sync stopped", status.message
    # idle
    detail = f"Checked {ago(status.last_check_at)}"
    if status.synced_total:
        detail += f" · {_plural(status.synced_total, 'screenshot')} backed up"
    if report and report.failed:
        return f"{_plural(report.failed, 'upload')} failed", detail + " · will try again later"
    if report and report.deferred:
        detail += f" · {_plural(report.deferred, 'screenshot')} to try again later"
    if report and report.problems:
        return "Up to date", report.problems[0]
    return "Up to date", detail


def open_state(settings: Settings) -> SyncState:
    """The sync memory for the server ``settings`` points at."""
    if not settings.server:
        return SyncState(":memory:")  # nothing to remember before the first sign-in
    return SyncState.open(app_paths.state_db_path(settings.server_key))


class SyncService:
    def __init__(
        self,
        settings: Settings,
        *,
        open_state: Callable[[Settings], SyncState] = open_state,
        client_factory: Callable[[str, str], GameVaultClient] = GameVaultClient,
        notify: Callable[[str, str], None] | None = None,
        save_settings: bool = True,
    ):
        self._settings = settings
        self._open_state = open_state
        self._client_factory = client_factory
        self._notify = notify or (lambda title, message: None)
        self._save_settings = save_settings
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._cancel = threading.Event()
        self._stopping = False
        self._run_now = False
        self._offline_streak = 0
        self._asked_server_id: str | None = None
        self._thread: threading.Thread | None = None
        self._db = open_state(settings)
        self._db_key = settings.server_key
        # Swapped-out memories stay open until the app quits: a pass or the
        # window may still be using one.
        self._retired: list[SyncState] = []
        self._status = Status(state="setup" if not settings.signed_in else "starting")
        self._status.synced_total = self._db.counts().get("synced", 0)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="sync", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        """Ask the loop to finish and wait up to ``timeout``. True once it has.

        An upload already on the wire can't be interrupted, so a caller that
        mustn't freeze (the window) passes a short timeout.
        """
        self._stopping = True
        self._cancel.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True

    def close(self) -> None:
        """Close the sync memory, unless the loop is still using it."""
        if self._thread is not None and self._thread.is_alive():
            return
        with self._lock:
            for db in (*self._retired, self._db):
                db.close()
            self._retired.clear()

    def set_notifier(self, notify: Callable[[str, str], None]) -> None:
        self._notify = notify

    # ── What the window and tray read ────────────────────────────────────────

    @property
    def settings(self) -> Settings:
        with self._lock:
            return copy.deepcopy(self._settings)

    def snapshot(self) -> Status:
        with self._lock:
            return replace(self._status)

    @property
    def state(self) -> SyncState:
        with self._lock:
            return self._db

    def _set(self, **changes) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(self._status, key, value)
            self._status.version += 1

    def _set_from_pass(self, **changes) -> None:
        """Like ``_set``, but a pause the user asked for mid-pass wins."""
        with self._lock:
            if self._status.state == "paused":
                changes.pop("state", None)
            self._set(**changes)

    # ── Commands ─────────────────────────────────────────────────────────────

    # Changing any of these restarts a pass that is under way.
    _RESCAN_FIELDS = frozenset({"server", "token", "steam_enabled", "steam_path", "specialk_enabled",
                                "specialk_path", "excluded_games", "first_sync_confirmed"})

    def update_settings(self, **changes) -> Settings:
        """Change settings, save them, and re-check soon."""
        return self._change_settings(changes, restart=True)

    def use_sign_in(self, **sign_in) -> Settings:
        """Switch to a new sign-in; another server gets its own sync memory."""
        with self._lock:
            return self._change_settings(self._settings.sign_in_changes(**sign_in), restart=True)

    def _change_settings(self, changes: dict, *, restart: bool) -> Settings:
        with self._lock:
            sign_in_changed = any(key in changes and changes[key] != getattr(self._settings, key)
                                  for key in ("server", "token"))
            for key, value in changes.items():
                setattr(self._settings, key, value)
            if self._save_settings:
                self._settings.save()
            self._use_matching_state()
            if not self._settings.signed_in:
                self._status.state = "setup"
                self._status.progress = None
            elif sign_in_changed and self._status.state in ("setup", "signed_out", "error"):
                # A new sign-in; a Notifications toggle while signed out isn't one.
                self._status.state = "starting"
                self._status.message = ""
            self._status.version += 1
            result = copy.deepcopy(self._settings)
        # After the change, so a pass that starts from here on sees it.
        if restart and self._RESCAN_FIELDS & changes.keys():
            self._cancel.set()  # a running pass restarts with the new settings
            self._wake.set()
        return result

    def _use_matching_state(self) -> None:
        """Switch to the sync memory of the server the settings point at (under the lock)."""
        key = self._settings.server_key
        if key == self._db_key:
            return
        new = self._open_state(self._settings)
        self._retired.append(self._db)
        self._db, self._db_key = new, key
        self._status.synced_total = new.counts().get("synced", 0)
        self._status.last_report = None
        log.info("using the sync memory for server %s", key)

    def sync_now(self) -> None:
        with self._lock:
            if self._status.state == "paused":
                self._status.paused_until = None
                self._status.state = "starting"
            self._run_now = True
        self._wake.set()

    def start_first_sync(self) -> None:
        self.update_settings(first_sync_confirmed=True)
        self.sync_now()

    def pause(self, seconds: float | None) -> None:
        until = time.time() + seconds if seconds else 0
        # Paused first, then cancel: a pass starting in between sees the pause.
        self._set(state="paused", paused_until=until, progress=None)
        self._cancel.set()
        self._wake.set()

    def resume(self) -> None:
        self._set(state="starting", paused_until=None)
        self._run_now = True
        self._wake.set()

    def recheck_everything(self) -> None:
        """Ask the server about every file again, e.g. after restoring a backup."""
        self.state.forget_sync_status()
        self.sync_now()

    def sign_out(self) -> None:
        settings = self.settings
        if settings.server and settings.token:
            try:
                with self._client_factory(settings.server, settings.token) as client:
                    client.sign_out()
            except Exception:
                log.info("couldn't tell the server about signing out", exc_info=True)
        self.update_settings(token="")

    # ── Loop ─────────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        next_at = 0.0
        while not self._stopping:
            timeout = max(0.0, next_at - time.time())
            self._wake.wait(timeout if next_at != float("inf") else None)
            self._wake.clear()
            if self._stopping:
                break
            try:
                next_at = self._tick()
            except Exception as exc:  # never let the loop die
                log.exception("sync pass crashed")
                self._set(state="error", message=str(exc) or exc.__class__.__name__, progress=None)
                next_at = time.time() + 300
            with self._lock:
                self._status.next_check_at = None if next_at == float("inf") else next_at
                self._status.version += 1

    def _tick(self) -> float:
        # Clear before reading: a change made from here on cancels this pass
        # instead of being overwritten by settings read a moment too early.
        self._cancel.clear()
        run_now, self._run_now = self._run_now, False
        now = time.time()
        settings = self.settings
        status = self.snapshot()

        if not settings.signed_in:
            if status.state != "signed_out":
                self._set(state="setup", progress=None)
            return float("inf")
        if status.state == "paused":
            if status.paused_until and status.paused_until <= now:
                self._set(state="starting", paused_until=None)
            else:
                return status.paused_until or float("inf")

        if not settings.first_sync_confirmed:
            # Count what would be uploaded; hashes are remembered, so the real
            # first sync doesn't read everything twice.
            return self._pass(settings, dry_run=True)
        if not settings.auto_sync and not run_now:
            if status.state in ("starting", "syncing"):
                self._set(state="idle", progress=None)
            return float("inf")
        return self._pass(settings, dry_run=False)

    def _pass(self, settings: Settings, dry_run: bool) -> float:
        self._set_from_pass(state="syncing", progress=Progress("scanning"), message="")
        try:
            settings = self._upgrade_old_token(settings)
            settings = self._learn_server_id(settings)
            with self._lock:
                self._use_matching_state()
                db = self._db
            with self._client_factory(settings.server, settings.token) as client:
                engine = SyncEngine(client, db, settings, cancel=self._cancel,
                                    on_progress=lambda p: self._set_from_pass(progress=p))
                try:
                    report = engine.run(dry_run=dry_run)
                except ApiError:
                    self._after_pass(engine.report, settings)
                    raise
        except SignedOutError as exc:
            if self.settings.token != settings.token:
                return time.time()  # signed in again meanwhile; that token wasn't the one refused
            self._set_from_pass(state="signed_out", progress=None,
                      message="This PC was signed out of GameVault (password changed, or removed in Settings).")
            if settings.notify:
                self._notify("GameVault Sync is signed out", "Open GameVault Sync to sign in again.")
            log.info("signed out: %s", exc)
            return float("inf")
        except SetupRequiredError as exc:
            self._set_from_pass(state="error", progress=None, message=str(exc))
            return time.time() + 600
        except ServerUnreachableError as exc:
            delay = _OFFLINE_BACKOFF[min(self._offline_streak, len(_OFFLINE_BACKOFF) - 1)]
            self._offline_streak += 1
            self._set_from_pass(state="offline", progress=None, message=str(exc))
            return time.time() + delay
        except ApiError as exc:
            self._set_from_pass(state="error", progress=None, message=str(exc))
            return time.time() + 300
        self._offline_streak = 0

        if report.cancelled:
            # Settings changed or a pause; the loop decides what's next.
            with self._lock:
                if self._status.state == "syncing":
                    self._status.state = "starting"
            return time.time()
        self._after_pass(report, settings)
        self._set_from_pass(
            state="ready" if dry_run else "idle",
            progress=None,
            last_report=report,
            last_check_at=time.time(),
            synced_total=db.counts().get("synced", 0),
        )
        # Files still being written are worth another look soon. (Failed
        # uploads have their own, growing, retry times.)
        if report.waiting:
            return time.time() + min(30, settings.interval_seconds)
        return time.time() + settings.interval_seconds

    def _after_pass(self, report: SyncReport | None, settings: Settings) -> None:
        if not report or not report.uploaded or not settings.notify:
            return
        games = report.uploaded_by_game
        if len(games) == 1:
            game = next(iter(games))
            message = f"{_plural(report.uploaded, 'screenshot')} from {game}"
        else:
            message = f"{_plural(report.uploaded, 'screenshot')} from {_plural(len(games), 'game')}"
        self._notify("Uploaded to GameVault", message)

    def _upgrade_old_token(self, settings: Settings) -> Settings:
        """Swap a token copied from the old script for a device token."""
        if settings.token.startswith("gvd_"):
            return settings
        with self._client_factory(settings.server, settings.token) as client:
            token = client.create_device_token(device_name(settings))
        log.info("replaced the old session token with a device token")
        return self._change_settings({"token": token}, restart=False)

    def _learn_server_id(self, settings: Settings) -> Settings:
        """Ask the server for its id once, for settings carried over without one."""
        if settings.server_id or self._asked_server_id == settings.server:
            return settings
        try:
            with self._client_factory(settings.server, settings.token) as client:
                server_id = client.server_instance()
        except ServerUnreachableError:
            raise
        except ApiError:
            log.info("couldn't ask %s for its id", settings.server, exc_info=True)
            server_id = None
        self._asked_server_id = settings.server
        if not server_id:
            return settings
        return self._change_settings({"server_id": server_id}, restart=False)


def check_server(raw: str) -> tuple[str, dict, dict]:
    """``(base_url, health, auth_status)`` for an address typed in the window."""
    return api.connect(raw)
