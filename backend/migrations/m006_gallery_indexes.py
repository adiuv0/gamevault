"""Add composite indexes for the dominant gallery sort paths."""

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    await db.execute(
        """CREATE INDEX IF NOT EXISTS idx_screenshots_game_taken_id
           ON screenshots(game_id, taken_at DESC, id DESC)"""
    )
    await db.execute(
        """CREATE INDEX IF NOT EXISTS idx_screenshots_game_filename
           ON screenshots(game_id, filename, id)"""
    )
