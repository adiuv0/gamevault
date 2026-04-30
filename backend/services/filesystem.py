"""Filesystem utilities for path sanitization and directory management."""

import re
from pathlib import Path

from fastapi import HTTPException

from backend.config import settings

# Characters invalid in Windows/Linux filenames
INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
TRAILING_DOTS_SPACES = re.compile(r'[\s.]+$')

# Windows reserved filenames
RESERVED_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
})


def sanitize_filename(name: str) -> str:
    """Sanitize a string for safe use as a filename.

    Removes invalid characters, handles reserved names, and ensures
    the result is non-empty.
    """
    sanitized = INVALID_CHARS.sub("", name)
    sanitized = TRAILING_DOTS_SPACES.sub("", sanitized)
    sanitized = sanitized.strip()

    if not sanitized:
        sanitized = "Unknown"

    # Handle Windows reserved names
    name_upper = sanitized.split(".")[0].upper()
    if name_upper in RESERVED_NAMES:
        sanitized = f"_{sanitized}"

    return sanitized


def sanitize_folder_name(name: str) -> str:
    """Sanitize a game name for use as a folder name.

    Same rules as filename but also collapses multiple spaces.
    """
    sanitized = sanitize_filename(name)
    # Collapse multiple spaces into one
    sanitized = re.sub(r"\s+", " ", sanitized)
    return sanitized


def get_game_dir(folder_name: str) -> Path:
    """Get the full path to a game's directory in the library."""
    return settings.library_dir / folder_name


def get_screenshots_dir(folder_name: str) -> Path:
    """Get the path to a game's screenshots directory."""
    return get_game_dir(folder_name) / "screenshots"


def get_thumbnails_dir(folder_name: str, size: str) -> Path:
    """Get the path to a game's thumbnails directory for a given size."""
    return get_game_dir(folder_name) / "thumbnails" / size


def get_metadata_dir(folder_name: str) -> Path:
    """Get the path to a game's metadata directory."""
    return get_game_dir(folder_name) / "metadata"


def ensure_game_directories(folder_name: str) -> None:
    """Create all required directories for a game.

    Creates: screenshots/, thumbnails/300/, thumbnails/800/, metadata/
    """
    dirs = [
        get_screenshots_dir(folder_name),
        get_thumbnails_dir(folder_name, "300"),
        get_thumbnails_dir(folder_name, "800"),
        get_metadata_dir(folder_name),
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def safe_library_path(rel_path: str | None) -> Path:
    """Resolve a DB-stored relative path under ``library_dir``, rejecting any
    that escape the library root.

    Defense in depth against GV-008: if a row in ``screenshots`` ever holds
    a poisoned path (absolute, or with traversal segments), the file-serve
    and delete paths must not let it reach outside the library.

    Raises HTTPException(404) on any unsafe path. The 404 (not 403) is
    intentional — callers should treat unsafe rows the same as missing rows.
    """
    if not rel_path:
        raise HTTPException(status_code=404, detail="File not found")

    library_root = settings.library_dir.resolve()
    candidate = (library_root / rel_path).resolve()

    try:
        candidate.relative_to(library_root)
    except ValueError:
        raise HTTPException(status_code=404, detail="File not found")

    return candidate


def get_library_size_bytes() -> int:
    """Calculate total size of the screenshot library on disk."""
    total = 0
    if settings.library_dir.exists():
        for f in settings.library_dir.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    return total


def format_file_size(size_bytes: int) -> str:
    """Format a file size in bytes to a human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
