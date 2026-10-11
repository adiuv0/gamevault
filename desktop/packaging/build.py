"""Build GameVault Sync into stand-alone programs.

    python packaging/build.py [--version 1.1.0]

Writes, under desktop/dist/:
  portable/GameVaultSync.exe   one file, runs from anywhere (slower to start)
  app/GameVaultSync/           a folder for the installer (packaging/installer.iss)

Run it on Windows for Windows programs; PyInstaller doesn't cross-compile.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
NAME = "GameVaultSync"

VERSION_INFO = """\
VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'GameVault'),
      StringStruct('FileDescription', 'GameVault Sync'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', '{name}'),
      StringStruct('OriginalFilename', '{name}.exe'),
      StringStruct('ProductName', 'GameVault Sync'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def stamp_version(version: str | None) -> str:
    target = ROOT / "gamevault_sync" / "_version.py"
    if version:
        target.write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    sys.path.insert(0, str(ROOT))
    from gamevault_sync import __version__

    return __version__


def numeric(version: str) -> tuple[int, int, int, int]:
    parts = []
    for piece in version.split(".")[:4]:
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple((parts + [0, 0, 0, 0])[:4])  # type: ignore[return-value]


def pyinstaller(mode: str, icon: Path, version_file: Path | None) -> None:
    dist = DIST / ("portable" if mode == "onefile" else "app")
    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--noupx",
        f"--{mode}", "--name", NAME, "--icon", str(icon),
        "--paths", str(ROOT),
        "--distpath", str(dist), "--workpath", str(BUILD / mode), "--specpath", str(BUILD),
        "--collect-data", "sv_ttk",
        "--collect-submodules", "keyring.backends",
        "--copy-metadata", "keyring",
        "--hidden-import", "pystray._win32" if sys.platform == "win32" else "pystray._xorg",
        # Pillow loads this only when the first image reaches a window.
        "--hidden-import", "PIL._tkinter_finder",
    ]
    if sys.platform == "win32":
        args += ["--hidden-import", "win32ctypes.core"]
        if version_file is not None:
            args += ["--version-file", str(version_file)]
    args.append(str(HERE / "launch.py"))
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True, cwd=ROOT)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", help="version to stamp into the build (default: the package's)")
    parser.add_argument("--only", choices=("onefile", "onedir"), help="build just one flavour")
    args = parser.parse_args()

    version = stamp_version(args.version)
    shutil.rmtree(DIST, ignore_errors=True)
    BUILD.mkdir(exist_ok=True)

    from gamevault_sync.icon import save_ico

    icon = BUILD / f"{NAME}.ico"
    save_ico(str(icon))
    version_file = BUILD / "version_info.txt"
    version_file.write_text(VERSION_INFO.format(nums=numeric(version), version=version, name=NAME),
                            encoding="utf-8")
    for mode in ([args.only] if args.only else ["onefile", "onedir"]):
        pyinstaller(mode, icon, version_file)
    print(f"Built GameVault Sync {version} in {DIST}")


if __name__ == "__main__":
    main()
