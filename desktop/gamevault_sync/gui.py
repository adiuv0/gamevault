"""The window: sign in once, then a status page you rarely need to open."""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import uuid
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from . import APP_NAME, __version__, app_paths, autostart
from .api import ApiError, GameVaultClient
from .engine import GameSummary
from .icon import app_icon
from .service import SyncService, ago, check_server, describe, device_name

log = logging.getLogger(__name__)

GREEN = "#3fb950"
AMBER = "#d29922"
RED = "#f85149"
MUTED = "#8b949e"
ACCENT = "#58a6ff"

_STATE_COLORS = {"idle": GREEN, "syncing": ACCENT, "ready": ACCENT, "paused": AMBER,
                 "offline": AMBER, "signed_out": RED, "error": RED}


def enable_high_dpi() -> None:
    """Crisp text on scaled Windows displays. Must run before the first window."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:
        pass


def _dark_title_bar(window: tk.Misc) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())  # type: ignore[attr-defined]
        value = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (newer, older builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(  # type: ignore[attr-defined]
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


def _shorten_path(path: str, limit: int = 46) -> str:
    if len(path) <= limit:
        return path
    keep = (limit - 1) // 2
    return path[:keep] + "…" + path[-(limit - 1 - keep):]


def open_folder(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        log.exception("couldn't open %s", path)


def _steam_root_from_choice(chosen: Path) -> Path | None:
    """Accept the Steam folder, or anything inside it such as userdata/…/screenshots."""
    for candidate in (chosen, *chosen.parents):
        if (candidate / "userdata").is_dir():
            return candidate
        if candidate.name.lower() == "userdata" and candidate.is_dir():
            return candidate.parent
    return None


def _specialk_root_from_choice(chosen: Path) -> Path:
    return chosen / "Profiles" if (chosen / "Profiles").is_dir() else chosen


class App:
    def __init__(self, service: SyncService, *, start_hidden: bool = False):
        self.service = service
        self.commands: queue.Queue[tuple] = queue.Queue()
        self.tray = None
        self._told_about_tray = False
        self._showing_error = False
        self._last_version = -1
        self._last_report_id: int | None = None
        self._icons: dict[str | None, tk.PhotoImage] = {}

        self.root = tk.Tk(className="GameVaultSync")
        self.root.withdraw()
        self.root.title(APP_NAME)
        self._apply_theme()
        self.root.geometry(f"{self._px(500)}x{self._px(640)}")
        self.root.minsize(self._px(440), self._px(540))
        self._window_icon = self._photo(64, None)
        self.root.iconphoto(True, self._window_icon)
        self.root.protocol("WM_DELETE_WINDOW", self.close_window)
        self.root.report_callback_exception = lambda kind, exc, tb: self.report_error(exc)

        self.body = ttk.Frame(self.root, padding=self._px(20))
        self.body.pack(fill="both", expand=True)
        self.setup_view: SetupView | None = None
        self.main_view: MainView | None = None
        self.setup_note = ""
        self._show_current_view()

        self.root.after(200, self._poll)
        if not start_hidden or not service.settings.signed_in:
            self.show()

    # ── Look ─────────────────────────────────────────────────────────────────

    def _apply_theme(self) -> None:
        try:
            import sv_ttk

            sv_ttk.set_theme("dark", self.root)
        except Exception:
            log.info("sv_ttk not available; using the default theme")
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        families = set(tkfont.families(self.root))
        fallback = next((f for f in ("Segoe UI", "Helvetica Neue", "Noto Sans", "DejaVu Sans")
                         if f in families), tkfont.nametofont("TkDefaultFont", self.root).cget("family"))
        for name in tkfont.names(self.root):
            if name.startswith("SunValley"):
                f = tkfont.nametofont(name, self.root)
                # The theme asks for Windows 11's font; older Windows and Linux lack it.
                family = f.cget("family")
                if family not in families:
                    f.configure(family=fallback, weight="bold" if "Semibold" in family else "normal")
                # Sizes are in pixels, which don't follow Windows display scaling.
                size = f.cget("size")
                if size < 0:
                    f.configure(size=round(size * self.scale))
        self.font_title = self._font("SunValleySubtitleFont", 20, "bold")
        self.font_strong = self._font("SunValleyBodyStrongFont", 14, "bold")
        self.font_caption = self._font("SunValleyCaptionFont", 12, "normal")
        style = ttk.Style(self.root)
        body = self._font("SunValleyBodyFont", 14, "normal")
        style.configure("Treeview", rowheight=body.metrics("linespace") + self._px(8))
        self.bg = style.lookup("TFrame", "background") or "#1c1c1c"
        self.root.configure(background=self.bg)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Caption.TLabel", foreground=MUTED, font=self.font_caption)
        style.configure("Error.TLabel", foreground=RED)
        style.configure("Ok.TLabel", foreground=GREEN)
        style.configure("Title.TLabel", font=self.font_title)
        style.configure("Headline.TLabel", font=self.font_title)
        style.configure("Strong.TLabel", font=self.font_strong)
        style.configure("Banner.TFrame", background="#1f2a3a")
        style.configure("Banner.TLabel", background="#1f2a3a")
        style.configure("BannerStrong.TLabel", background="#1f2a3a", font=self.font_strong)

    def _font(self, name: str, fallback_size: int, weight: str) -> tkfont.Font:
        try:
            return tkfont.nametofont(name, self.root)
        except tk.TclError:
            return tkfont.Font(self.root, size=-round(fallback_size * self.scale), weight=weight)

    def _px(self, value: float) -> int:
        return round(value * getattr(self, "scale", 1.0))

    def _photo(self, size: int, status: str | None) -> tk.PhotoImage:
        key = f"{size}:{status}"
        if key not in self._icons:
            from PIL import ImageTk

            self._icons[key] = ImageTk.PhotoImage(app_icon(self._px(size), status), master=self.root)
        return self._icons[key]

    # ── Views ────────────────────────────────────────────────────────────────

    def _show_current_view(self) -> None:
        signed_in = self.service.settings.signed_in
        if signed_in and self.main_view is None:
            if self.setup_view is not None:
                self.setup_view.frame.destroy()
                self.setup_view = None
            self.main_view = MainView(self, self.body)
            self._last_version = -1
        elif not signed_in and self.setup_view is None:
            if self.main_view is not None:
                self.main_view.frame.destroy()
                self.main_view = None
            self.setup_view = SetupView(self, self.body)

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        _dark_title_bar(self.root)

    def close_window(self) -> None:
        if not self.service.settings.signed_in:
            self.quit()
        elif self.tray is not None and self.tray.visible:
            self.root.withdraw()
            if not self._told_about_tray:
                self._told_about_tray = True
                self.tray.notify(APP_NAME, "Still running in the tray. It keeps syncing in the background.")
        else:
            # No tray to come back from (some Linux desktops): minimise instead of hiding.
            self.root.iconify()

    def quit(self) -> None:
        # Gone at once. The tray icon and an upload still on the wire are left
        # to the shutdown in __main__, rather than freezing the window.
        self.root.withdraw()
        self.service.stop(timeout=0)
        self.root.quit()

    def report_error(self, exc: BaseException) -> None:
        """A button or background job failed: log it and say so, once at a time."""
        log.error("error in the window", exc_info=(type(exc), exc, exc.__traceback__))
        if self._showing_error:
            return
        self._showing_error = True
        try:
            messagebox.showerror(
                APP_NAME, f"Something went wrong: {exc or exc.__class__.__name__}\n\n"
                f"Details are in the log (More > Open log folder).",
                parent=self.root if self.root.winfo_viewable() else None)
        except tk.TclError:
            pass
        finally:
            self._showing_error = False

    def open_web(self) -> None:
        server = self.service.settings.server
        if server:
            webbrowser.open(server)

    def run_in_background(self, work: Callable[[], object], done: Callable[[object, BaseException | None], None]) -> None:
        """Run ``work`` off the UI thread and hand its result back on it."""

        def runner() -> None:
            try:
                result, error = work(), None
            except BaseException as exc:  # noqa: BLE001 - reported to the UI
                result, error = None, exc
            self.commands.put(("callback", lambda: done(result, error)))

        threading.Thread(target=runner, daemon=True).start()

    # ── Loop ─────────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        # Whatever fails in here, the next poll is scheduled: a window that
        # stops polling ignores the tray, sign-in results and status changes.
        try:
            while True:
                try:
                    command = self.commands.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle(command)
                except Exception as exc:
                    self.report_error(exc)
            self._refresh()
        except Exception:
            log.exception("couldn't refresh the window")
        finally:
            try:
                self.root.after(250, self._poll)
            except tk.TclError:
                pass  # the window is gone

    def _refresh(self) -> None:
        status = self.service.snapshot()
        if status.state in ("setup",) and self.main_view is not None:
            self._show_current_view()
        if status.version != self._last_version:
            self._last_version = status.version
            if self.main_view is not None:
                self.main_view.render(status)
            if self.tray is not None:
                headline, _detail = describe(status)
                self.tray.update(status.state, f"{APP_NAME}: {headline}")
        elif self.main_view is not None:
            self.main_view.tick(status)

    def _handle(self, command: tuple) -> None:
        kind = command[0]
        if kind == "show":
            self.show()
        elif kind == "sync":
            self.service.sync_now()
        elif kind == "pause":
            self.service.pause(command[1])
        elif kind == "resume":
            self.service.resume()
        elif kind == "web":
            self.open_web()
        elif kind == "quit":
            self.quit()
        elif kind == "callback":
            command[1]()

    def signed_in(self) -> None:
        self.setup_note = ""
        self._show_current_view()
        self.show()

    def apply_autostart_default(self) -> None:
        """Turn Start with Windows on once, at the first sign-in; later it's the user's call."""
        if self.service.settings.autostart_initialized or not autostart.supported():
            return
        try:
            autostart.set_enabled(True)
        except OSError:
            log.warning("couldn't turn on start with Windows", exc_info=True)
            return
        self.service.update_settings(autostart_initialized=True)
        if self.main_view is not None:
            self.main_view.autostart_var.set(True)

    def run(self) -> None:
        self.root.mainloop()


class SetupView:
    def __init__(self, app: App, parent: ttk.Frame):
        self.app = app
        self.server_info: tuple[str, dict, dict] | None = None
        self._checking = False
        self._busy = False
        # What to do once the address checks out (sign in), kept while a check
        # started by leaving the address box is still running.
        self._then: Callable[[], None] | None = None
        settings = app.service.settings
        px = app._px

        self.frame = ttk.Frame(parent)
        self.frame.pack(fill="both", expand=True)

        header = ttk.Frame(self.frame)
        header.pack(fill="x", pady=(px(8), px(24)))
        ttk.Label(header, image=app._photo(56, None)).pack(side="left", padx=(0, px(14)))
        titles = ttk.Frame(header)
        titles.pack(side="left", fill="x")
        ttk.Label(titles, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text="Back up your game screenshots to GameVault,\nautomatically.",
                  style="Muted.TLabel", justify="left").pack(anchor="w")

        if app.setup_note:
            ttk.Label(self.frame, text=app.setup_note, style="Error.TLabel", wraplength=px(420),
                      justify="left").pack(anchor="w", pady=(0, px(14)))

        ttk.Label(self.frame, text="Server address", style="Strong.TLabel").pack(anchor="w")
        ttk.Label(self.frame, text="The address you open GameVault at in your browser.",
                  style="Caption.TLabel").pack(anchor="w", pady=(0, px(6)))
        self.server_var = tk.StringVar(value=settings.server)
        self.server_entry = ttk.Entry(self.frame, textvariable=self.server_var)
        self.server_entry.pack(fill="x")
        self.server_entry.bind("<FocusOut>", lambda e: self.check_server())
        self.server_entry.bind("<Return>", lambda e: self.password_entry.focus_set())
        self.server_status = ttk.Label(self.frame, text=" ", style="Caption.TLabel")
        self.server_status.pack(anchor="w", pady=(px(4), px(14)))
        self.server_var.trace_add("write", lambda *a: self._server_edited())

        self.password_box = ttk.Frame(self.frame)
        self.password_box.pack(fill="x")
        ttk.Label(self.password_box, text="Password", style="Strong.TLabel").pack(anchor="w")
        ttk.Label(self.password_box, text="Your GameVault password. It isn't stored on this PC.",
                  style="Caption.TLabel").pack(anchor="w", pady=(0, px(6)))
        self.password_var = tk.StringVar()
        self.password_entry = ttk.Entry(self.password_box, textvariable=self.password_var, show="•")
        self.password_entry.pack(fill="x")
        self.password_entry.bind("<Return>", lambda e: self.sign_in())

        actions = ttk.Frame(self.frame)
        actions.pack(fill="x", pady=(px(22), 0))
        self.sign_in_button = ttk.Button(actions, text="Sign in", style="Accent.TButton",
                                         command=self.sign_in)
        self.sign_in_button.pack(side="right")
        self.busy = ttk.Progressbar(actions, mode="indeterminate", length=px(120))

        self.error = ttk.Label(self.frame, text="", style="Error.TLabel", wraplength=px(420),
                               justify="left")
        self.error.pack(anchor="w", pady=(px(14), 0))

        footer = ttk.Frame(self.frame)
        footer.pack(side="bottom", fill="x")
        ttk.Label(footer, text=f"Version {__version__} · This PC will show up in GameVault as "
                  f"“{device_name(settings)}”.", style="Caption.TLabel",
                  wraplength=px(440), justify="left").pack(anchor="w")

        (self.password_entry if settings.server else self.server_entry).focus_set()
        if settings.server:
            self.check_server()

    def _server_edited(self) -> None:
        self.server_info = None
        self.server_status.configure(text=" ", style="Caption.TLabel")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self.busy.pack(side="right", padx=(0, self.app._px(12)))
            self.busy.start(12)
            self.sign_in_button.state(["disabled"])
        else:
            self.busy.stop()
            self.busy.pack_forget()
            self.sign_in_button.state(["!disabled"])

    def check_server(self, then: Callable[[], None] | None = None) -> None:
        if then is not None:
            self._then = then
        raw = self.server_var.get().strip()
        if not raw:
            self._give_up()
            return
        if self.server_info is not None:
            self._continue()
            return
        if self._checking:
            return  # the check under way picks up ``self._then`` when it's done
        self._checking = True
        self.server_status.configure(text="Looking for GameVault…", style="Caption.TLabel")

        def done(result, error) -> None:
            self._checking = False
            if raw != self.server_var.get().strip():
                # Edited meanwhile: check the new address if a sign-in is waiting on it.
                if self._then is not None:
                    self.check_server()
                return
            if error is not None:
                message = str(error) if isinstance(error, ApiError) else f"Couldn't connect: {error}"
                self.server_status.configure(text=message, style="Error.TLabel")
                self._give_up()
                return
            base, health, auth = result
            if auth.get("setup_required"):
                self.server_status.configure(
                    text="GameVault doesn't have a password yet. Open it in your browser and set one first.",
                    style="Error.TLabel")
                self._give_up()
                return
            self.server_info = result
            version = health.get("version")
            text = f"✓ Found GameVault{(' ' + version) if version else ''} at {base}"
            if auth.get("auth_disabled"):
                text += " · no password needed"
                self.password_box.pack_forget()
            self.server_status.configure(text=text, style="Ok.TLabel")
            self._continue()

        self.app.run_in_background(lambda: check_server(raw), done)

    def _continue(self) -> None:
        then, self._then = self._then, None
        if then is not None:
            then()

    def _give_up(self) -> None:
        self._then = None
        self._set_busy(False)

    def sign_in(self) -> None:
        if self._busy:
            return  # Enter pressed again while signing in
        self.error.configure(text="")
        if not self.server_var.get().strip():
            self.error.configure(text="Enter your server's address first.")
            self.server_entry.focus_set()
            return
        self._set_busy(True)
        self.check_server(then=self._pair)

    def _pair(self) -> None:
        assert self.server_info is not None
        base, health, auth = self.server_info
        password = self.password_var.get()
        if not password and not auth.get("auth_disabled"):
            self._set_busy(False)
            self.error.configure(text="Enter your GameVault password.")
            self.password_entry.focus_set()
            return
        settings = self.app.service.settings
        name = device_name(settings)
        install_id = settings.install_id or uuid.uuid4().hex
        server_id = health.get("instance") if isinstance(health.get("instance"), str) else ""

        def work() -> str:
            with GameVaultClient(base) as client:
                return client.pair(password, name, install_id)

        def done(token, error) -> None:
            self._set_busy(False)
            if error is not None:
                if isinstance(error, ApiError):
                    text = str(error)
                else:
                    text = f"Couldn't sign in: {error}"
                self.error.configure(text=text)
                self.password_var.set("")
                self.password_entry.focus_set()
                return
            self.app.service.use_sign_in(server=base, token=token, device_name=name,
                                         server_id=server_id, install_id=install_id)
            self.app.apply_autostart_default()
            self.app.signed_in()

        self.app.run_in_background(work, done)


class MainView:
    def __init__(self, app: App, parent: ttk.Frame):
        self.app = app
        px = app._px
        self._last_tick = 0.0
        self._report_shown = None
        self.frame = ttk.Frame(parent)
        self.frame.pack(fill="both", expand=True)

        # Status header.
        header = self.header = ttk.Frame(self.frame)
        header.pack(fill="x")
        self.status_icon = ttk.Label(header, image=app._photo(48, "setup"))
        self.status_icon.pack(side="left", padx=(0, px(14)))
        text = ttk.Frame(header)
        text.pack(side="left", fill="x", expand=True)
        self.headline = ttk.Label(text, text="Starting…", style="Headline.TLabel")
        self.headline.pack(anchor="w")
        self.detail = ttk.Label(text, text="", style="Muted.TLabel", wraplength=px(360), justify="left")
        self.detail.pack(anchor="w")
        self.progress = ttk.Progressbar(self.frame, mode="determinate")

        # First-sync and signed-out banners.
        self.banner = ttk.Frame(self.frame, style="Banner.TFrame", padding=px(14))
        self.banner_text = ttk.Label(self.banner, text="", style="Banner.TLabel", wraplength=px(420),
                                     justify="left")
        self.banner_text.pack(anchor="w", fill="x")
        self.banner_actions = ttk.Frame(self.banner, style="Banner.TFrame")
        self.banner_actions.pack(anchor="w", pady=(px(10), 0))

        # Main actions.
        actions = self.actions = ttk.Frame(self.frame)
        actions.pack(fill="x", pady=(px(14), px(16)))
        self.sync_button = ttk.Button(actions, text="Sync now", style="Accent.TButton",
                                      command=app.service.sync_now)
        self.sync_button.pack(side="left")
        self.pause_button = ttk.Menubutton(actions, text="Pause")
        pause_menu = tk.Menu(self.pause_button, tearoff=False)
        pause_menu.add_command(label="For 1 hour", command=lambda: app.service.pause(3600))
        pause_menu.add_command(label="For 4 hours", command=lambda: app.service.pause(4 * 3600))
        pause_menu.add_command(label="Until I resume", command=lambda: app.service.pause(None))
        self.pause_button["menu"] = pause_menu
        self.pause_button.pack(side="left", padx=(px(8), 0))
        self.resume_button = ttk.Button(actions, text="Resume", command=app.service.resume)
        ttk.Button(actions, text="Open GameVault", command=app.open_web).pack(side="right")

        # Where screenshots come from.
        sources = ttk.LabelFrame(self.frame, text="Screenshot folders", padding=(px(12), px(8)))
        sources.pack(fill="x")
        sources.columnconfigure(1, weight=1)
        settings = app.service.settings
        self.steam_var = tk.BooleanVar(value=settings.steam_enabled)
        self.specialk_var = tk.BooleanVar(value=settings.specialk_enabled)
        ttk.Checkbutton(sources, text="Steam", variable=self.steam_var,
                        command=lambda: app.service.update_settings(steam_enabled=self.steam_var.get())
                        ).grid(row=0, column=0, sticky="w")
        self.steam_path = ttk.Label(sources, text="", style="Caption.TLabel")
        self.steam_path.grid(row=0, column=1, sticky="w", padx=px(8))
        ttk.Button(sources, text="Change…", command=self.choose_steam).grid(row=0, column=2, pady=px(2))
        ttk.Checkbutton(sources, text="Special K", variable=self.specialk_var,
                        command=lambda: app.service.update_settings(specialk_enabled=self.specialk_var.get())
                        ).grid(row=1, column=0, sticky="w")
        self.specialk_path = ttk.Label(sources, text="", style="Caption.TLabel")
        self.specialk_path.grid(row=1, column=1, sticky="w", padx=px(8))
        ttk.Button(sources, text="Change…", command=self.choose_specialk).grid(row=1, column=2, pady=px(2))
        self.games_label = ttk.Label(sources, text="", style="Caption.TLabel")
        self.games_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(px(6), 0))
        ttk.Button(sources, text="Choose games…", command=self.choose_games).grid(
            row=2, column=2, pady=(px(6), 0))

        # Preferences.
        footer = ttk.Frame(self.frame)
        footer.pack(side="bottom", fill="x", pady=(px(12), 0))
        self.autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        start_label = "Start with Windows" if sys.platform == "win32" else "Start when I log in"
        start_check = ttk.Checkbutton(footer, text=start_label, variable=self.autostart_var,
                                      command=self.toggle_autostart)
        start_check.pack(side="left")
        if not autostart.supported():
            start_check.state(["disabled"])
        self.notify_var = tk.BooleanVar(value=settings.notify)
        ttk.Checkbutton(footer, text="Notifications", variable=self.notify_var,
                        command=lambda: app.service.update_settings(notify=self.notify_var.get())
                        ).pack(side="left", padx=(px(14), 0))
        more = ttk.Menubutton(footer, text="More")
        menu = tk.Menu(more, tearoff=False)
        menu.add_command(label="Check every file again", command=self.recheck)
        menu.add_command(label="Open log folder", command=lambda: open_folder(app_paths.data_dir()))
        menu.add_separator()
        menu.add_command(label="Sign out of this PC…", command=self.sign_out)
        menu.add_command(label=f"About {APP_NAME}", command=self.about)
        menu.add_separator()
        menu.add_command(label="Quit", command=app.quit)
        more["menu"] = menu
        more.pack(side="right")

        # Recent uploads.
        recent = ttk.LabelFrame(self.frame, text="Recently uploaded", padding=(px(8), px(6)))
        recent.pack(fill="both", expand=True, pady=(px(14), 0))
        self.recent = ttk.Treeview(recent, columns=("when", "game", "file"), show="headings",
                                   selectmode="none", height=6)
        for column, title, width, stretch in (("when", "When", 90, False), ("game", "Game", 150, True),
                                              ("file", "File", 170, True)):
            self.recent.heading(column, text=title, anchor="w")
            self.recent.column(column, width=px(width), stretch=stretch, anchor="w")
        self.recent.tag_configure("rejected", foreground=AMBER)
        self.recent.pack(fill="both", expand=True)
        self.empty_recent = ttk.Label(recent, text="Nothing uploaded yet.", style="Caption.TLabel")

        self.render(app.service.snapshot())

    # ── Drawing ──────────────────────────────────────────────────────────────

    def render(self, status) -> None:
        px = self.app._px
        headline, detail = describe(status)
        self.headline.configure(text=headline)
        self.detail.configure(text=detail)
        self.status_icon.configure(image=self.app._photo(48, status.state if status.state != "starting" else "syncing"))

        progress = status.progress if status.state == "syncing" else None
        if progress is not None:
            if not self.progress.winfo_ismapped():
                self.progress.pack(fill="x", pady=(px(12), 0), after=self.header)
            if progress.total:
                self.progress.stop()
                self.progress.configure(mode="determinate", maximum=progress.total, value=progress.done)
            elif str(self.progress.cget("mode")) != "indeterminate":
                self.progress.configure(mode="indeterminate")
                self.progress.start(12)
        elif self.progress.winfo_ismapped():
            self.progress.stop()
            self.progress.pack_forget()

        if status.state == "paused":
            self.pause_button.pack_forget()
            self.resume_button.pack(side="left", padx=(px(8), 0))
        else:
            self.resume_button.pack_forget()
            self.pause_button.pack(side="left", padx=(px(8), 0))
        busy = status.state == "syncing" or status.state == "ready"
        self.sync_button.state(["disabled"] if busy else ["!disabled"])

        self._render_banner(status)
        report = status.last_report
        if report is not None and report is not self._report_shown:
            self._report_shown = report
            self._render_sources(report)
        self._render_recent()

    def _render_banner(self, status) -> None:
        px = self.app._px
        report = status.last_report
        kind = (status.state, report.new if report is not None else None, status.message)
        if kind == getattr(self, "_banner_kind", None):
            return
        self._banner_kind = kind
        for child in self.banner_actions.winfo_children():
            child.destroy()
        if status.state == "ready" and report is not None and report.new:
            games = sum(1 for g in report.games if g.new and not g.excluded)
            self.banner_text.configure(
                text=f"Found {report.new:,} screenshots in {games:,} games that aren't in GameVault yet. "
                     "Start syncing to upload them, newest first. New screenshots will then upload "
                     "on their own.")
            ttk.Button(self.banner_actions, text="Start syncing", style="Accent.TButton",
                       command=self.app.service.start_first_sync).pack(side="left")
            ttk.Button(self.banner_actions, text="Choose games…", command=self.choose_games).pack(
                side="left", padx=(px(8), 0))
        elif status.state == "ready" and report is not None:
            self.banner_text.configure(text="Everything in these folders is already in GameVault. "
                                            "Turn on syncing to upload new screenshots as you take them.")
            ttk.Button(self.banner_actions, text="Turn on syncing", style="Accent.TButton",
                       command=self.app.service.start_first_sync).pack(side="left")
        elif status.state == "signed_out":
            self.banner_text.configure(text=status.message or "This PC was signed out of GameVault.")
            ttk.Button(self.banner_actions, text="Sign in again", style="Accent.TButton",
                       command=self.sign_in_again).pack(side="left")
        else:
            if self.banner.winfo_ismapped():
                self.banner.pack_forget()
            return
        if not self.banner.winfo_ismapped():
            self.banner.pack(fill="x", pady=(px(14), 0), before=self.actions)

    def _render_sources(self, report) -> None:
        settings = self.app.service.settings

        def describe_path(enabled: bool, found: str | None, chosen: str, missing: str) -> str:
            if not enabled:
                return "Off"
            if found:
                return _shorten_path(found)
            return _shorten_path(chosen) + " (not found)" if chosen else missing

        self.steam_path.configure(text=describe_path(settings.steam_enabled, report.steam_root,
                                                     settings.steam_path, "Not found on this PC"))
        self.specialk_path.configure(text=describe_path(settings.specialk_enabled, report.specialk_root,
                                                        settings.specialk_path, "Not found; choose its Profiles folder"))
        found = [g for g in report.games if g.found]
        skipped = sum(1 for g in found if g.excluded)
        shots = sum(g.found for g in found if not g.excluded)
        text = f"{len(found):,} games, {shots:,} screenshots"
        if skipped:
            text += f" · skipping {skipped:,} {'game' if skipped == 1 else 'games'}"
        self.games_label.configure(text=text)

    def _render_recent(self) -> None:
        entries = self.app.service.state.recent(40)
        key = tuple((e.at, e.filename) for e in entries[:3]) + (len(entries),)
        if getattr(self, "_recent_key", None) == key:
            return
        self._recent_key = key
        self.recent.delete(*self.recent.get_children())
        for entry in entries:
            name = (entry.filename if entry.outcome == "uploaded"
                    else f"{entry.filename} (not accepted: {entry.detail or 'unknown reason'})")
            self.recent.insert("", "end", values=(ago(entry.at), entry.game, name),
                               tags=(() if entry.outcome == "uploaded" else ("rejected",)))
        if entries:
            self.empty_recent.place_forget()
        else:
            self.empty_recent.place(relx=0.5, rely=0.55, anchor="center")

    def tick(self, status) -> None:
        """Refresh relative times now and then."""
        if time.monotonic() - self._last_tick < 20:
            return
        self._last_tick = time.monotonic()
        _headline, detail = describe(status)
        self.detail.configure(text=detail)
        self._recent_key = None
        self._render_recent()

    # ── Actions ──────────────────────────────────────────────────────────────

    def choose_steam(self) -> None:
        chosen = filedialog.askdirectory(parent=self.app.root, title="Choose the folder Steam is installed in")
        if not chosen:
            return
        root = _steam_root_from_choice(Path(chosen))
        if root is None:
            messagebox.showerror(APP_NAME, "That folder doesn't look like Steam. Choose the folder Steam "
                                 "is installed in; it has a folder called userdata inside.", parent=self.app.root)
            return
        self.steam_var.set(True)
        self.app.service.update_settings(steam_path=str(root), steam_enabled=True)

    def choose_specialk(self) -> None:
        chosen = filedialog.askdirectory(parent=self.app.root,
                                         title="Choose Special K's Profiles folder (one folder per game)")
        if not chosen:
            return
        root = _specialk_root_from_choice(Path(chosen))
        self.specialk_var.set(True)
        self.app.service.update_settings(specialk_path=str(root), specialk_enabled=True)

    def choose_games(self) -> None:
        report = self.app.service.snapshot().last_report
        GamePicker(self.app, report.games if report else [])

    def toggle_autostart(self) -> None:
        try:
            autostart.set_enabled(self.autostart_var.get())
        except OSError as exc:
            self.autostart_var.set(autostart.is_enabled())
            messagebox.showerror(APP_NAME, f"Couldn't change this: {exc}", parent=self.app.root)

    def recheck(self) -> None:
        if messagebox.askyesno(
                APP_NAME, "Check every screenshot against GameVault again?\n\nAnything that isn't on the "
                "server any more will be uploaded again, including screenshots you deleted there on purpose.",
                parent=self.app.root):
            self.app.service.recheck_everything()

    def sign_out(self) -> None:
        if not messagebox.askyesno(APP_NAME, "Sign this PC out? Syncing stops until you sign in again.",
                                   parent=self.app.root):
            return
        self.app.run_in_background(self.app.service.sign_out, lambda result, error: self.app._show_current_view())

    def sign_in_again(self) -> None:
        self.app.setup_note = self.app.service.snapshot().message
        self.app.service.update_settings(token="")
        self.app._show_current_view()

    def about(self) -> None:
        messagebox.showinfo(
            APP_NAME,
            f"{APP_NAME} {__version__}\n\nUploads new Steam and Special K screenshots to your GameVault "
            f"server.\n\nSettings and logs: {app_paths.data_dir()}",
            parent=self.app.root,
        )


def _without(layout: list, element: str) -> list:
    out = []
    for name, options in layout:
        if name == element:
            continue
        options = dict(options)
        if "children" in options:
            options["children"] = _without(options["children"], element)
        out.append((name, options))
    return out


def _checkbox_image(app: App, checked: bool) -> tk.PhotoImage:
    from PIL import Image, ImageDraw, ImageTk

    size = app._px(18)
    big = size * 4
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    box = [4, 4, big - 5, big - 5]
    if checked:
        draw.rounded_rectangle(box, radius=big // 6, fill=ACCENT)
        draw.line([(big * 0.27, big * 0.52), (big * 0.43, big * 0.68), (big * 0.74, big * 0.34)],
                  fill="#0d1117", width=max(4, big // 9), joint="curve")
    else:
        draw.rounded_rectangle(box, radius=big // 6, outline=MUTED, width=max(3, big // 14))
    return ImageTk.PhotoImage(img.resize((size, size), Image.LANCZOS), master=app.root)


class GamePicker:
    """Tick the games to sync; unticked ones are skipped."""

    def __init__(self, app: App, games: list[GameSummary]):
        self.app = app
        px = app._px
        self.window = tk.Toplevel(app.root)
        self.window.title("Choose games")
        self.window.configure(background=app.bg)
        self.window.transient(app.root)
        self.window.geometry(f"{px(460)}x{px(520)}")
        self.excluded = set(app.service.settings.excluded_games)
        body = ttk.Frame(self.window, padding=px(16))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Choose which games to back up", style="Strong.TLabel").pack(anchor="w")
        ttk.Label(body, text="Unticked games are skipped. Click a row to change it.",
                  style="Caption.TLabel").pack(anchor="w", pady=(0, px(10)))

        self.boxes = {True: _checkbox_image(app, True), False: _checkbox_image(app, False)}
        style = ttk.Style(app.root)
        style.layout("Picker.Treeview.Item", _without(style.layout("Item"), "Treeitem.indicator"))
        style.configure("Picker.Treeview", indent=0)
        self.tree = ttk.Treeview(body, columns=("source", "count"), show=("tree", "headings"),
                                 selectmode="none", style="Picker.Treeview")
        self.tree.heading("#0", text="Game", anchor="w")
        self.tree.column("#0", width=px(250), stretch=True, anchor="w")
        for column, title, width, stretch in (("source", "From", 80, False), ("count", "Screenshots", 90, False)):
            self.tree.heading(column, text=title, anchor="w")
            self.tree.column(column, width=px(width), stretch=stretch, anchor="w")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        actions = ttk.Frame(body)
        actions.pack(side="bottom", fill="x", pady=(px(12), 0))
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Button-1>", self._toggle)
        self.tree.bind("<space>", self._toggle)

        self.games = {g.key: g for g in games if g.found}
        for game in sorted(self.games.values(), key=lambda g: g.name.lower()):
            source = "Steam" if game.source == "steam" else "Special K"
            self.tree.insert("", "end", iid=game.key, text=f" {game.name}", image=self._mark(game.key),
                             values=(source, f"{game.found:,}"))
        if not self.games:
            ttk.Label(body, text="No games found yet. Check the folders in the main window.",
                      style="Caption.TLabel").place(relx=0.5, rely=0.5, anchor="center")

        ttk.Button(actions, text="Save", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(actions, text="Cancel", command=self.window.destroy).pack(side="right", padx=(0, px(8)))
        ttk.Button(actions, text="All", command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(actions, text="None", command=lambda: self._set_all(False)).pack(side="left", padx=(px(8), 0))
        _dark_title_bar(self.window)
        self.window.grab_set()

    def _mark(self, key: str) -> tk.PhotoImage:
        return self.boxes[key not in self.excluded]

    def _toggle(self, event) -> None:
        if event.type == tk.EventType.ButtonPress:
            if self.tree.identify_region(event.x, event.y) == "heading":
                return
            row = self.tree.identify_row(event.y)
        else:
            row = self.tree.focus()
        if not row:
            return
        self.excluded ^= {row}
        self.tree.item(row, image=self._mark(row))

    def _set_all(self, picked: bool) -> None:
        self.excluded = set() if picked else set(self.games)
        for key in self.games:
            self.tree.item(key, image=self._mark(key))

    def save(self) -> None:
        # Keep exclusions for games that aren't on disk right now.
        previous = set(self.app.service.settings.excluded_games)
        excluded = (previous - set(self.games)) | self.excluded
        self.app.service.update_settings(excluded_games=sorted(excluded))
        self.window.destroy()
