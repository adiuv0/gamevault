# GameVault Sync

The sync tool is now a desktop app with an installer, a tray icon and a
one-time password sign-in. It lives in [`desktop/`](../desktop/README.md).

`python cli/gamevault_sync.py` still works and starts that app, and the old
options (`--no-gui`, `--server`, `--token`, `--mode`, `--steam-path`,
`--specialk-path`, `--dry-run`) are still accepted. Install its packages first
with `pip install -r desktop/requirements.txt`.
