"""Persist HDR classification for conservative capture matching."""

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    await db.execute(
        """ALTER TABLE screenshots ADD COLUMN is_hdr INTEGER
           CHECK(is_hdr IN (0, 1))"""
    )
    await db.execute(
        """UPDATE screenshots SET is_hdr = 1
           WHERE lower(COALESCE(format, '')) IN ('jxr', 'wdp')
              OR lower(filename) LIKE '%.jxr'
              OR lower(filename) LIKE '%.wdp'"""
    )
