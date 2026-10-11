# GameVault Sync

GameVault Sync runs on your gaming PC and backs up new screenshots to your
GameVault server on its own. It finds Steam and Special K by itself, signs in
once with your GameVault password, and then sits in the notification area,
uploading new captures about a minute after you take them.

## Install (Windows)

Download **GameVaultSync-Setup.exe** from the
[latest release](https://github.com/adiuv0/gamevault/releases/latest) (GameVault's
Settings page links to it too) and run it. It installs for your user only, so it
doesn't ask for admin rights. If you'd rather not install anything,
**GameVaultSync.exe** from the same release is a portable copy that runs from
anywhere; it starts a little slower.

The programs aren't code-signed, so Windows SmartScreen may say it "protected
your PC" the first time. Click **More info**, then **Run anyway**.

## First run

1. Enter your server's address, the same one you open GameVault at in your
   browser (for example `http://tower:8080`). If you leave off `http://`, a
   home-network name like `tower:8080` tries both; a public name like
   `vault.example.com` gets `https://` only, so your password never crosses
   the internet unencrypted. Type `http://` yourself if you really mean it.
2. Enter your GameVault password and click **Sign in**. The password isn't
   stored; the PC gets its own sign-in, which shows up under
   **Settings > GameVault Sync** in GameVault, where you can sign it out.
3. The app counts what isn't in GameVault yet and waits. Click **Choose games**
   to skip any, then **Start syncing**. Screenshots go up newest first.

From then on there's nothing to do. Closing the window keeps it running in the
tray; **Start with Windows** is on by default.

## What it does and doesn't do

- **Sources.** Steam screenshots from every Steam account on the PC, including
  non-Steam games you added to Steam, and Special K captures (SDR `.png` and
  HDR `.jxr`), one folder per game under Special K's `Profiles` folder. If a
  folder isn't found automatically, click **Change…** next to it.
- **Only new files are read.** The app remembers each file's size and modified
  time, so a check over thousands of screenshots takes a moment and reads
  nothing. A file is uploaded once.
- **Failures are retried, patiently.** If an upload fails for a reason that
  might pass (the server was busy, its disk was full), the file is tried
  again after 1 minute, then 4, then 16, and so on up to every 6 hours, so one
  broken file never gets sent every minute. Files the server can't take
  (not an image, too big) are listed as not accepted and left alone until
  they change.
- **Deleting in GameVault sticks.** A screenshot you delete in GameVault isn't
  uploaded again. **More > Check every file again** re-checks everything if you
  ever want that (after restoring a backup, say).
- **Dates.** Captures without a date inside the file (PNG, JXR) get the time
  Steam recorded, the date in the file name, or the file's modified time.
- **The sign-in can only upload.** It can't view, change or delete anything in
  your library. Changing your GameVault password signs every PC out.

## Settings and logs

Everything lives in `%LOCALAPPDATA%\GameVault Sync` (on Linux
`~/.config/gamevault-sync`, on macOS `~/Library/Application Support/GameVault Sync`):
`config.json`, the sync memory (`sync-state-<server id>.db`, one per server, so
signing in to another server and back forgets nothing), and
`gamevault-sync.log`. **More > Open log folder** opens it. The sign-in itself is
kept in Windows Credential Manager (Keychain on macOS, Secret Service on Linux).
Uninstalling keeps this folder, so a reinstall doesn't re-check every file. If
the sync memory is ever damaged, the app sets it aside (`.corrupt-…`) and
starts a new one; that costs one slower check, nothing else.

HTTPS certificates are checked against the ones Windows trusts, so a
certificate you've installed for your home server or company proxy works.

## Terminal use, Linux and macOS

On Windows the window is all most people need. For a terminal, the easiest
route is the Python package below. The `.exe` also takes these options, but
it's a windowed program, so Command Prompt doesn't wait for it: run it as
`start /wait GameVaultSync.exe --sync-once`, or redirect its output
(`GameVaultSync.exe --sync-once > sync.log`).

The app is a Python package and runs anywhere Python 3.10+ with Tk does:

```bash
pip install ./desktop          # from a checkout of this repository
gamevault-sync                 # the window
gamevault-sync --sign-in tower:8080   # sign in from a terminal
gamevault-sync --sync-once            # one pass, then exit (good for cron)
gamevault-sync --sync-once --dry-run  # list what would be uploaded
gamevault-sync --watch                # keep syncing in this terminal
```

The window and tray work on Linux desktops that show tray icons. Without a tray
the window minimises instead of hiding.

Signing in through the window (or `--sign-in`) is better than a token: it is
listed in GameVault under this PC's name. For a headless machine, create a
token under **Settings > GameVault Sync > Create a token for a script** and
pass it for a single run, nothing saved:

```bash
gamevault-sync --sync-once --server http://tower:8080 --token gvd_… \
    --mode specialk --specialk-path "/mnt/games/SpecialK/Profiles"
```

`--mode`, `--steam-path`, `--specialk-path`, `--server`, `--token` and
`--no-gui` are the old `cli/gamevault_sync.py` options and still work;
`python cli/gamevault_sync.py` now starts this app.

## Building

`python packaging/build.py` builds `dist/portable/GameVaultSync.exe` and
`dist/app/GameVaultSync/` with PyInstaller (on Windows, for Windows), and
`iscc /DAppVersion=1.2.3 packaging\installer.iss` turns the folder into
`dist/GameVaultSync-Setup.exe` with [Inno Setup 6](https://jrsoftware.org/isinfo.php).
The `Desktop app` GitHub workflow does both on every `v*` tag and attaches the
two programs to that release.

Tests: `pip install -r requirements.txt pytest`, then `python -m pytest` in
this folder.
