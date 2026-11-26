"""Queue a repair that reads capture times stored inside the files themselves.

Special K and the Windows capture path write the moment of capture into an XMP
packet (older Special K builds: a PNG ``Creation Time`` chunk), which GameVault
did not read, so those uploads were stored with ``taken_at`` NULL and sorted by
upload day. The repair (backend.services.embedded_capture_times) runs in the
background after startup and only fills ``taken_at`` where it is NULL. A fresh
duplicate scan is queued behind it so the newly dated copies pair by time; it
replaces any scan still waiting from m020.

No schema change: older releases read the filled-in times like any other.
"""

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("SELECT COUNT(*) FROM screenshots WHERE taken_at IS NULL")
    untimed = (await cursor.fetchone())[0]
    if not untimed:
        return
    await db.execute(
        "INSERT INTO maintenance_jobs (kind, total) VALUES ('embedded_capture_times', ?)",
        (untimed,),
    )
    cursor = await db.execute("SELECT COUNT(*) FROM screenshots")
    total = (await cursor.fetchone())[0]
    await db.execute(
        """UPDATE maintenance_jobs SET status = 'completed', completed_at = datetime('now'),
               updated_at = datetime('now')
           WHERE kind = 'duplicate_backfill' AND status IN ('pending', 'running', 'failed')"""
    )
    await db.execute(
        "INSERT INTO maintenance_jobs (kind, total) VALUES ('duplicate_backfill', ?)",
        (total,),
    )
