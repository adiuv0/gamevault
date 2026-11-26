"""Queue a library scan that pairs saved copies which have no capture time.

Files uploaded before capture times were read have ``taken_at`` NULL and a name
built from the upload time, so ``m017``'s scan could match them to the Steam JPG
of the same press by neither time nor name, and each one was counted as its own
capture. ``capture_matching`` now pairs them when the picture match is
unambiguous. Like ``m017``, this only queues the scan the server runs in the
background after startup; it changes ``capture_group_id`` alone, older releases
read the result as ordinary groups, and every pairing can be undone with Ungroup.
"""

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("SELECT COUNT(*) FROM screenshots")
    total = (await cursor.fetchone())[0]
    if not total:
        return
    await db.execute(
        """UPDATE maintenance_jobs SET status = 'completed', completed_at = datetime('now'),
               updated_at = datetime('now')
           WHERE kind = 'duplicate_backfill' AND status IN ('pending', 'running', 'failed')"""
    )
    await db.execute(
        "INSERT INTO maintenance_jobs (kind, total) VALUES ('duplicate_backfill', ?)",
        (total,),
    )
