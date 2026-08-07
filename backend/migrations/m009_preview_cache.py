"""Track disposable browser-friendly large previews."""

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    await db.execute("ALTER TABLE screenshots ADD COLUMN preview_path TEXT")
