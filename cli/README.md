# GameVault Sync

Standalone Python script to sync local Steam screenshots to your GameVault server.

## Requirements

- Python 3.10+
- `httpx` (`pip install httpx`)

## Usage

### GUI Mode (default)

```bash
python gamevault_sync.py
```

1. Enter your GameVault server URL (e.g., `http://unraid:8080`)
2. Enter your JWT auth token (from GameVault Settings page)
3. Steam path is auto-detected, or browse to select
4. Click **Scan** to find local screenshots and compare against GameVault
5. Check/uncheck games you want to sync
6. Click **Sync** to upload new screenshots

Settings are saved to `~/.gamevault_sync.json` for next run.

### CLI Mode

```bash
python gamevault_sync.py --no-gui --server http://unraid:8080 --token <jwt>
```

Options:
- `--no-gui` — Run without GUI
- `--server URL` — GameVault server URL (required in CLI mode)
- `--token JWT` — Authentication token (required in CLI mode)
- `--steam-path PATH` — Path to Steam installation (auto-detected if not specified)
- `--dry-run` — Show what would be uploaded without uploading

### Getting Your Auth Token

1. Log into your GameVault instance
2. Open browser DevTools (F12) → Application → Local Storage
3. Copy the value of `gamevault_token`

Or generate one from the Settings page if available.
