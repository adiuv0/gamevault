"""Give each game an accent colour taken from its cover art.

The game page and library tint their highlights with it. ``accent_color`` is a
``#rrggbb`` string. NULL means it has not been worked out yet; an empty string
means the cover was read and had no usable colour (greyscale, unreadable or
missing), so nothing retries it on every start. Reading covers needs the
library folder, so existing games are filled in after startup
(backend.services.cover_accent).
"""

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    await db.execute("ALTER TABLE games ADD COLUMN accent_color TEXT")
