"""SQLite connections are task-owned: commits never affect another request.

A task borrows one connection for its lifetime. When the task ends, any
uncommitted work is rolled back and the connection returns to a small idle pool,
so requests (including every thumbnail) skip opening a connection, starting its
worker thread, and warming SQLite's per-connection page cache from scratch.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from functools import wraps

import aiosqlite

from backend.config import settings
from backend.migrations.runner import run_migrations

logger = logging.getLogger(__name__)

_POOL_LIMIT = 8

_db: aiosqlite.Connection | None = None
_connections: dict[asyncio.Task, aiosqlite.Connection] = {}
_idle: list[aiosqlite.Connection] = []
_idle_path: str | None = None
_closing: set[asyncio.Task] = set()


async def _connect() -> aiosqlite.Connection:
    db = await aiosqlite.connect(str(settings.db_path))
    await db.execute("PRAGMA foreign_keys=ON")
    await db.execute("PRAGMA busy_timeout=5000")
    # WAL with synchronous=NORMAL never corrupts the database; at worst the last
    # commits before a power loss roll back. Originals live on disk regardless.
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA cache_size=-16000")
    await db.execute("PRAGMA temp_store=MEMORY")
    return db


async def _recycle(db: aiosqlite.Connection) -> None:
    """Roll back abandoned work, then keep the connection for the next task."""
    try:
        if db.in_transaction:
            await db.rollback()
        if _db is not None and _idle_path == str(settings.db_path) and len(_idle) < _POOL_LIMIT:
            _idle.append(db)
            return
    except Exception:
        logger.warning("Discarding a database connection that failed to reset", exc_info=True)
    await db.close()


def _release(task: asyncio.Task) -> None:
    db = _connections.pop(task, None)
    if db is not None:
        # Rollback covers cancelled requests: their uncommitted work never lands.
        closer = asyncio.create_task(_recycle(db))
        _closing.add(closer)
        closer.add_done_callback(_closing.discard)


async def database_session():
    try:
        yield
    finally:
        db = _connections.pop(asyncio.current_task(), None)
        if db is not None:
            await _recycle(db)


async def get_db() -> aiosqlite.Connection:
    """Reuse a connection only within the current asyncio task."""
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    task = asyncio.current_task()
    if task not in _connections:
        _connections[task] = _idle.pop() if _idle else await _connect()
        task.add_done_callback(_release)
    return _connections[task]


@asynccontextmanager
async def transaction():
    """Acquire SQLite's writer lock before reading data used by an atomic write."""
    db = await get_db()
    await db.execute("BEGIN IMMEDIATE")
    try:
        yield db
        await db.commit()
    except BaseException:
        await db.rollback()
        raise


def atomic(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        async with transaction():
            return await function(*args, **kwargs)
    return wrapped


async def init_db() -> None:
    global _db, _idle_path
    await close_db()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    _db = await _connect()
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.commit()
    await run_migrations(_db)
    _idle_path = str(settings.db_path)


async def close_db() -> None:
    global _db, _idle_path
    _idle_path = None
    connections = list(_connections.values())
    _connections.clear()
    for connection in connections:
        await connection.close()
    if _closing:
        await asyncio.gather(*list(_closing))
    idle = list(_idle)
    _idle.clear()
    for connection in idle:
        await connection.close()
    if _db is not None:
        await _db.close()
        _db = None
