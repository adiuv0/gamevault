"""SQLite database connection management and initialization."""

import aiosqlite

from backend.config import settings
from backend.migrations.runner import run_migrations

# Global connection reference
_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    """Get the database connection. Raises if not initialized."""
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _db


async def init_db() -> None:
    """Initialize the database connection and run migrations."""
    global _db

    # Ensure data directory exists
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)

    _db = await aiosqlite.connect(str(settings.db_path))

    # Enable WAL mode for better read concurrency
    await _db.execute("PRAGMA journal_mode=WAL")
    # Enable foreign keys
    await _db.execute("PRAGMA foreign_keys=ON")
    # Reasonable busy timeout for concurrent access
    await _db.execute("PRAGMA busy_timeout=5000")

    await _db.commit()

    # Run migrations
    await run_migrations(_db)


async def close_db() -> None:
    """Close the database connection."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None
