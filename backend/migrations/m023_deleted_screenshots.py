"""Remember the files a person deleted so sync and imports don't bring them back.

GameVault Sync uploads every local file whose hash the server doesn't have, and a
Steam or Special K import re-imports anything missing. Without a record, a deleted
screenshot came back on the next sync. Each deleted file's sha256 goes here; the
hash check and the importers treat it as already stored. Uploading the file again
by hand (the Upload page) removes it from the list.

A new table only: older releases ignore it.
"""

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    await db.execute(
        """CREATE TABLE deleted_screenshots (
               sha256_hash TEXT PRIMARY KEY,
               filename    TEXT,
               game_id     INTEGER,
               deleted_at  TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
