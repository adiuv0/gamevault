"""Coalesce expensive filesystem scans and keep them off the event loop."""

import asyncio
import time

from backend.config import settings
from backend.services.filesystem import get_library_size_bytes, get_thumbnail_cache_size_bytes

_cached = None
_pending = None
_TTL_SECONDS = 300


def _start_scan(key: tuple[str, str]) -> asyncio.Task:
    global _pending
    if _pending is None or _pending[0] != key or _pending[1].done():
        def scan():
            return get_library_size_bytes(), get_thumbnail_cache_size_bytes()

        async def run():
            global _cached
            sizes = await asyncio.to_thread(scan)
            _cached = (key, time.monotonic(), sizes)
            return sizes

        task = asyncio.create_task(run())
        # A failed background refresh keeps serving the last value; don't warn twice.
        task.add_done_callback(lambda done: done.cancelled() or done.exception())
        _pending = (key, task)
    return _pending[1]


async def get_storage_sizes() -> tuple[int, int]:
    """Return library and cache sizes, serving stale values while a rescan runs.

    Only the very first request for a library waits for a scan. After that, an
    expired value is returned immediately and refreshed in the background, so a
    walk of a large (possibly spun-down) array never blocks a page load.
    """
    key = (str(settings.library_dir), str(settings.thumbnail_dir))
    if _cached and _cached[0] == key:
        if time.monotonic() - _cached[1] >= _TTL_SECONDS:
            _start_scan(key)
        return _cached[2]
    return await asyncio.shield(_start_scan(key))
