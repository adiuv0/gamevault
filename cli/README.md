# GameVault Sync

Standalone Python script to sync local screenshots to your GameVault
server. Supports two sources:

- **Steam** — walks `<Steam>/userdata/<id>/760/remote/<appid>/screenshots/`
- **Special K** — walks a user-supplied root where each top-level
  subfolder is a game (handles both `.png` SDR and `.jxr` HDR captures)

You can run either source individually or both at the same time. Files
already in your GameVault library (matched by SHA-256 hash) are skipped,
so re-running the sync is safe and idempotent.

## Requirements

- Python 3.10+
- `httpx` (`pip install httpx`) — required
- `keyring` (`pip install keyring`) — **optional**, recommended.
  When installed, the auth token is stored in your OS credential vault
  (Credential Manager on Windows, Keychain on macOS, Secret Service on
  Linux) instead of plaintext in `~/.gamevault_sync.json`. The CLI
  detects keyring at runtime and falls back to JSON storage with a
  warning if it's not available.

## Usage

### GUI Mode (default)

```bash
python gamevault_sync.py
```

1. Enter your GameVault server URL (e.g., `http://unraid:8080`)
2. Enter your JWT auth token (from GameVault Settings page)
3. Pick the **Mode**: Steam Only / Special K Only / Steam + Special K
4. Fill in whichever paths your selected mode needs:
   - **Steam Path** is auto-detected on Windows; browse if needed
   - **Special K Path** is required when scanning Special K — point it at
     your Special K profiles/screenshots root (each top-level subfolder
     becomes a game)
5. Click **Scan** to discover screenshots, hash them locally, and compare
   against GameVault
6. Check/uncheck games you want to sync (each row shows a `[Steam]` or
   `[SpecialK]` tag plus `N new / M total`)
7. Click **Sync** to upload new screenshots

Settings (server, both paths, and mode) are saved to
`~/.gamevault_sync.json` for the next run. The auth token is stored
separately in the OS keyring when `keyring` is installed; otherwise it
goes into the same JSON file as a plaintext fallback.

### CLI Mode

Steam only (default):

```bash
python gamevault_sync.py --no-gui \
    --server http://unraid:8080 --token <jwt>
```

Special K only:

```bash
python gamevault_sync.py --no-gui --mode specialk \
    --server http://unraid:8080 --token <jwt> \
    --specialk-path "C:/Users/You/Documents/My Mods/SpecialK/Profiles"
```

Both at once:

```bash
python gamevault_sync.py --no-gui --mode both \
    --server http://unraid:8080 --token <jwt> \
    --specialk-path "C:/Users/You/Documents/My Mods/SpecialK/Profiles"
```

Add `--dry-run` to scan and report without uploading.

#### Options

| Flag | Notes |
|---|---|
| `--no-gui` | Run without the tkinter window |
| `--server URL` | GameVault server URL (required in CLI mode) |
| `--token JWT` | Authentication token (required in CLI mode) |
| `--mode {steam,specialk,both}` | Which source(s) to sync (default: `steam`) |
| `--steam-path PATH` | Steam install dir (auto-detected if omitted) |
| `--specialk-path PATH` | Special K screenshots root (required for `specialk` or `both`) |
| `--dry-run` | Scan and report only |

### How Special K imports work

Each top-level subdirectory under `--specialk-path` is treated as a
separate game. The folder name is cleaned client-side to a likely game
name (`Cyberpunk2077` → `Cyberpunk 2077`, etc.) and matched against your
existing GameVault library — so a Special K folder named the same as a
Steam-imported game merges into the same library entry.

JXR (HDR) and PNG (SDR + HDR) files inside each game folder are walked
recursively, regardless of whether Special K is configured to write into
`<game>/HDR/`, `<game>/Screenshots/HDR/`, or just `<game>/`.

HDR JXR files are uploaded as-is. The GameVault server tone-maps them to
SDR JPEG for thumbnails and the gallery view, while preserving the
original on disk for download. The tone-mapping algorithm and exposure
are configured in GameVault's Settings page.

### Getting Your Auth Token

1. Log into your GameVault instance
2. Go to **Settings → Sync Token** and click **Copy Token**

Or read it from the browser:

1. Open DevTools (F12) → Application → Local Storage
2. Copy the value of `gamevault_token`
