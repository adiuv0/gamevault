"""Database migration runner.

Uses a simple version tracking table to run migrations in order.
Each migration is a Python module with an `upgrade(db)` async function.
"""

import importlib

import aiosqlite

# List of migration modules in order
MIGRATIONS = [
    "backend.migrations.m001_initial",
    "backend.migrations.m002_gallery",
]


async def run_migrations(db: aiosqlite.Connection) -> None:
    """Run any pending migrations."""
    # Create migrations tracking table if it doesn't exist
    await db.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    await db.commit()

    # Get already-applied migrations
    cursor = await db.execute("SELECT name FROM _migrations")
    applied = {row[0] for row in await cursor.fetchall()}

    # Run pending migrations
    for migration_name in MIGRATIONS:
        if migration_name in applied:
            continue

        module = importlib.import_module(migration_name)
        await module.upgrade(db)

        await db.execute(
            "INSERT INTO _migrations (name) VALUES (?)",
            (migration_name,),
        )
        await db.commit()
