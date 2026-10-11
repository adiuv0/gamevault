"""Start GameVault Sync: the window by default, or a terminal mode."""

from __future__ import annotations

import argparse
import getpass
import logging
import logging.handlers
import os
import signal
import sys
import threading
import traceback
import uuid

from . import APP_NAME, __version__, app_paths
from .config import Settings

log = logging.getLogger("gamevault_sync")


def _setup_logging(verbose: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    try:
        handler = logging.handlers.RotatingFileHandler(app_paths.log_path(), maxBytes=1_000_000,
                                                       backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    except OSError:
        pass
    if verbose and sys.stderr is not None:
        root.addHandler(logging.StreamHandler())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)


def _attach_console() -> None:
    """Let the windowed .exe print when it's started from a terminal.

    Output redirected to a file or pipe (``> log.txt``) already works and is
    left alone.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        import ctypes

        if ctypes.windll.kernel32.AttachConsole(-1):  # type: ignore[attr-defined]
            # Stays open for the life of the process.
            console = open("CONOUT$", "w", encoding="utf-8", errors="replace")  # noqa: SIM115
            sys.stdout = sys.stdout or console
            sys.stderr = sys.stderr or console
            print()
    except Exception:
        pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gamevault-sync",
        description=f"{APP_NAME}: back up Steam and Special K screenshots to a GameVault server. "
                    "With no options it opens the window.",
    )
    parser.add_argument("--background", action="store_true",
                        help="start in the notification area without opening the window")
    parser.add_argument("--sync-once", action="store_true", help="sync once in this terminal, then exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --sync-once: list what would be uploaded, upload nothing")
    parser.add_argument("--watch", action="store_true",
                        help="keep syncing in this terminal (no window), until Ctrl+C")
    parser.add_argument("--sign-in", metavar="SERVER", help="sign this PC in from the terminal")
    parser.add_argument("--verbose", action="store_true", help="log details to the terminal")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    old = parser.add_argument_group("options from the old gamevault_sync.py (still work, not saved)")
    old.add_argument("--no-gui", action="store_true", help="same as --sync-once")
    old.add_argument("--server", help="server address for this run")
    old.add_argument("--token", help="device token (or old session token) for this run")
    old.add_argument("--mode", choices=("steam", "specialk", "both"), help="which sources to sync")
    old.add_argument("--steam-path", help="Steam install folder")
    old.add_argument("--specialk-path", help="Special K Profiles folder")
    return parser


def _settings_for_run(args: argparse.Namespace) -> tuple[Settings, bool]:
    """Saved settings with this run's overrides. Second value: anything overridden."""
    settings = Settings.load()
    overridden = False
    if args.server and args.server.rstrip("/").lower() != settings.server.rstrip("/").lower():
        # Another server: its id is unknown, and this PC's sign-in is not for it.
        if not args.token and settings.token:
            print(f"Note: this PC is signed in to {settings.server}, not {args.server}; "
                  "pass --token for that server.", file=sys.stderr)
        settings.server, settings.server_id, overridden = args.server.rstrip("/"), "", True
        if not args.token:
            settings.token = ""
    if args.token:
        settings.token, overridden = args.token, True
    if args.mode:
        settings.steam_enabled = args.mode in ("steam", "both")
        settings.specialk_enabled = args.mode in ("specialk", "both")
        overridden = True
    if args.steam_path:
        settings.steam_path, settings.steam_enabled, overridden = args.steam_path, True, True
    if args.specialk_path:
        settings.specialk_path, settings.specialk_enabled, overridden = args.specialk_path, True, True
    return settings, overridden


def _sign_in(raw_server: str) -> int:
    from .api import ApiError, GameVaultClient, connect
    from .service import device_name

    try:
        base, health, auth = connect(raw_server)
    except ApiError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Found GameVault {health.get('version', '')} at {base}")
    if auth.get("setup_required"):
        print("GameVault has no password yet. Open it in a browser and set one first.", file=sys.stderr)
        return 1
    password = "" if auth.get("auth_disabled") else getpass.getpass("GameVault password: ")
    settings = Settings.load()
    name = device_name(settings)
    install_id = settings.install_id or uuid.uuid4().hex
    try:
        with GameVaultClient(base) as client:
            token = client.pair(password, name, install_id)
    except ApiError as exc:
        print(f"Couldn't sign in: {exc}", file=sys.stderr)
        return 1
    server_id = health.get("instance") if isinstance(health.get("instance"), str) else ""
    for key, value in settings.sign_in_changes(server=base, token=token, device_name=name,
                                               server_id=server_id, install_id=install_id).items():
        setattr(settings, key, value)
    settings.save()
    print(f"Signed in. This PC shows up in GameVault's Settings as “{name}”.")
    return 0


def _print_progress(progress) -> None:
    if progress.phase == "reading":
        print(f"\rReading new files: {progress.done:,}/{progress.total:,}", end="", flush=True)
    elif progress.phase == "uploading" and progress.total and progress.done:
        print(f"\r[{progress.done:,}/{progress.total:,}] {progress.current[:70]:<70}", end="", flush=True)


def _print_rejection(game: str, filename: str, outcome: str, detail: str | None) -> None:
    if outcome == "rejected":
        print(f"\n  not accepted: {filename} ({detail})", file=sys.stderr)


def _sync_once(args: argparse.Namespace) -> int:
    from .api import ApiError, GameVaultClient
    from .engine import SyncEngine
    from .service import open_state

    settings, _overridden = _settings_for_run(args)
    if not settings.signed_in:
        print("This PC isn't signed in. Run with --sign-in SERVER first, "
              "or pass --server and --token.", file=sys.stderr)
        return 2
    cancel = threading.Event()
    signal.signal(signal.SIGINT, lambda *a: cancel.set())
    state = None
    try:
        with GameVaultClient(settings.server, settings.token) as client:
            if not settings.server_id:
                # Find this server's own sync memory, whatever address was typed.
                settings.server_id = client.server_instance() or ""
            state = open_state(settings)
            engine = SyncEngine(client, state, settings, cancel=cancel, on_progress=_print_progress,
                                on_file=_print_rejection)
            report = engine.run(dry_run=args.dry_run)
    except ApiError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    finally:
        if state is not None:
            state.close()
    print()
    for problem in report.problems:
        print(f"Note: {problem}")
    if report.steam_root:
        print(f"Steam: {report.steam_root}")
    if report.specialk_root:
        print(f"Special K: {report.specialk_root}")
    if args.dry_run:
        for game in report.games:
            if game.new and not game.excluded:
                print(f"  {game.name:<44} {game.new:>6,} new / {game.found:,}")
        print(f"\n{report.found:,} screenshots found, {report.new:,} not in GameVault yet. Nothing uploaded (dry run).")
        return 0
    print(f"{report.found:,} screenshots found · {report.uploaded:,} uploaded · "
          f"{report.already + report.duplicates:,} already in GameVault · {report.rejected:,} not accepted"
          + (f" · {report.failed:,} failed" if report.failed else "")
          + (f" · {report.deferred:,} failed before, tried again later" if report.deferred else "")
          + (f" · {report.waiting:,} busy, try again" if report.waiting else ""))
    if report.cancelled:
        print("Stopped.")
        return 130
    return 1 if report.failed else 0


def _watch(args: argparse.Namespace) -> int:
    from .service import SyncService, describe

    settings, overridden = _settings_for_run(args)
    if not settings.signed_in:
        print("This PC isn't signed in. Run with --sign-in SERVER first.", file=sys.stderr)
        return 2
    settings.first_sync_confirmed = True
    service = SyncService(settings, save_settings=not overridden,
                          notify=lambda title, message: print(f"\n{title}: {message}"))
    service.start()
    print(f"Syncing every {settings.interval_seconds} s. Press Ctrl+C to stop.")
    last = None
    try:
        while True:
            status = service.snapshot()
            line = " · ".join(part for part in describe(status) if part)
            if line != last:
                print(f"\r{line[:100]:<100}", end="", flush=True)
                last = line
            threading.Event().wait(0.5)
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        if service.stop():
            service.close()
    return 0


def _run_window(args: argparse.Namespace) -> int:
    from . import autostart
    from .gui import App, enable_high_dpi
    from .service import SyncService
    from .single_instance import SingleInstance
    from .tray import start_tray

    instance = SingleInstance()
    if not instance.acquire():
        return 0  # the running copy has been brought to the front
    enable_high_dpi()
    service = SyncService(Settings.load())
    app = App(service, start_hidden=args.background)
    instance.on_show(lambda: app.commands.put(("show",)))
    app.tray = start_tray(app.commands)
    if app.tray is not None:
        service.set_notifier(app.tray.notify)
    if args.background:
        # Started hidden; if no tray icon turned up to open it from (a slow
        # login, or a Linux desktop without one), show it minimised.
        def no_tray_fallback() -> None:
            if app.tray is None or not app.tray.visible:
                app.root.iconify()

        app.root.after(8000, no_tray_fallback)
    if service.settings.signed_in:
        app.apply_autostart_default()  # settings carried over from the old script
    try:
        autostart.refresh()
    except OSError:
        log.info("couldn't refresh the start-with-Windows entry", exc_info=True)
    log.info("%s %s started", APP_NAME, __version__)
    service.start()
    try:
        app.run()
    finally:
        # The tray icon goes first, so quitting feels instant. (pystray can wait
        # a while for a tray that never appeared; that's not worth waiting for.)
        tray_stopper = None
        if app.tray is not None:
            tray_stopper = threading.Thread(target=app.tray.stop, name="tray-stop", daemon=True)
            tray_stopper.start()
        # An upload on the wire can't be interrupted; give it a moment, then go.
        stopped = service.stop(timeout=3)
        if tray_stopper is not None:
            tray_stopper.join(1)
        instance.release()
        if stopped:
            service.close()
        log.info("%s stopped", APP_NAME)
        logging.shutdown()
        for stream in (sys.stdout, sys.stderr):
            try:
                if stream is not None:
                    stream.flush()
            except Exception:
                pass
    # Leftover threads (the tray, an unfinished upload) mustn't keep the
    # process alive or crash it on the way out.
    os._exit(0)


def _self_test() -> int:
    """Check a packaged build has everything it needs, without opening a window."""
    import importlib

    for module in ("gamevault_sync.gui", "gamevault_sync.tray", "PIL.ImageTk", "tkinter", "httpx", "truststore"):
        importlib.import_module(module)
    import ssl

    import keyring
    import sv_ttk

    from .api import _ssl_verify
    from .icon import app_icon

    assert isinstance(_ssl_verify(), ssl.SSLContext), "the OS certificate store isn't being used"

    assert app_icon(32, "idle").size == (32, 32)
    assert sv_ttk.TCL_THEME_FILE_PATH.exists(), "sv_ttk theme files are missing"
    if sys.platform == "win32" or os.environ.get("DISPLAY"):
        # Exercise the parts that only load once a window exists.
        import tkinter

        from PIL import ImageTk

        root = tkinter.Tk()
        root.withdraw()
        sv_ttk.set_theme("dark", root)
        ImageTk.PhotoImage(app_icon(32, "idle"), master=root)
        root.destroy()
    backend = type(keyring.get_keyring())
    name = f"{backend.__module__}.{backend.__name__}"
    if sys.platform == "win32":
        assert "fail" not in backend.__module__, f"no Windows keyring backend ({name})"
    print(f"{APP_NAME} {__version__}: OK (keyring: {name})")
    return 0


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    if any(arg != "--background" for arg in raw):
        _attach_console()  # any option but --background comes from a terminal
    args = _parser().parse_args(argv)
    if args.self_test:
        try:
            return _self_test()
        except BaseException:  # noqa: BLE001 - a windowed build would show a dialog nobody clicks in CI
            traceback.print_exc(file=sys.stdout)
            print(f"{APP_NAME} {__version__}: FAILED")
            return 1
    _setup_logging(args.verbose)
    if args.sign_in:
        return _sign_in(args.sign_in)
    if args.sync_once or args.no_gui:
        return _sync_once(args)
    if args.watch:
        return _watch(args)
    try:
        return _run_window(args)
    except ImportError as exc:
        log.exception("missing module")
        if getattr(sys, "frozen", False):
            _fatal(f"This copy of {APP_NAME} is missing a part ({exc.name}). Please download it again.")
        else:
            print(f"The window needs more packages ({exc.name}). Install them with:\n"
                  "    pip install ./desktop\nor use --sync-once / --watch in a terminal.", file=sys.stderr)
        return 1


def _fatal(message: str) -> None:
    """Tell someone who double-clicked the app, who has no terminal to read."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)  # type: ignore[attr-defined]
            return
        except Exception:
            pass
    print(message, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
