"""Add is_public column to games for public gallery feature."""

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    await db.execute(
        "ALTER TABLE games ADD COLUMN is_public INTEGER NOT NULL DEFAULT 1"
    )
    await db.commit()
