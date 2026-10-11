"""Full-resolution browser renders for originals browsers can't display.

Browsers can't decode JPEG XR or TIFF, so the viewer used to stop at the
bounded preview (``preview_max_width``, 1920px by default) for those
captures. This service renders such originals once, at full size, into the
thumbnail cache and serves the cached JPEG after that. Renders are made on
first view rather than at import so imports and grids stay fast.
"""

import asyncio
from pathlib import Path

from fastapi import HTTPException

from backend.services.filesystem import (
    FULL_RENDER_DIR,
    safe_library_path,
    thumbnail_cache_path,
)
from backend.services.image_processor import derivative_filename, generate_full_render

BROWSER_INCOMPATIBLE_FORMATS = {"jxr", "wdp", "tif", "tiff"}

# A 4K scRGB decode holds ~100 MB of float32 pixels (8K ~400 MB). Bound the
# number of concurrent renders so flipping quickly through HDR captures can't
# exhaust a small server's memory.
_render_slots = asyncio.Semaphore(2)
_render_locks: dict[str, asyncio.Lock] = {}


def needs_full_render(screenshot: dict) -> bool:
    """True when the original can't be shown by a browser as-is."""
    image_format = (screenshot.get("format") or "").lower()
    suffix = Path(screenshot.get("file_path") or "").suffix.lower().lstrip(".")
    return image_format in BROWSER_INCOMPATIBLE_FORMATS or suffix in BROWSER_INCOMPATIBLE_FORMATS


def full_render_rel_path(screenshot: dict) -> str | None:
    """Return the render's DB-shaped path (``Game/thumbnails/full/<name>``).

    The name matches the content-addressed preview name, so duplicates share
    one render and no hashing is needed when a v2 preview already exists.
    """
    file_path = screenshot.get("file_path")
    if not file_path:
        return None
    preview = Path(screenshot.get("preview_path") or "")
    folder = Path(file_path).parts[0]
    if preview.name.startswith("v2-") and len(preview.parts) >= 4:
        name = preview.name
        folder = preview.parts[0]
    else:
        source = safe_library_path(file_path)
        if not source.exists():
            return None
        name = derivative_filename(source)
    return f"{folder}/thumbnails/{FULL_RENDER_DIR}/{name}"


async def ensure_full_render(screenshot: dict) -> Path | None:
    """Return the cached full-size render, creating it on first request.

    Returns None when the original is missing or can't be decoded, so the
    caller can fall back to the preview.
    """
    try:
        rel_path = await asyncio.to_thread(full_render_rel_path, screenshot)
        target = thumbnail_cache_path(rel_path)
        source = safe_library_path(screenshot.get("file_path"))
    except HTTPException:
        return None
    if rel_path is None or target is None:
        return None
    if target.exists():
        return target

    lock = _render_locks.setdefault(rel_path, asyncio.Lock())
    async with lock:
        try:
            if target.exists():
                return target
            if not source.exists():
                return None
            async with _render_slots:
                ok = await asyncio.to_thread(generate_full_render, source, target)
            return target if ok else None
        finally:
            # Requests already waiting on this lock re-check target.exists();
            # later ones find the file before taking any lock.
            _render_locks.pop(rel_path, None)
