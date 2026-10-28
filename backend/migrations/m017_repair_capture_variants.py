"""Queue a library scan that joins saved copies of one capture.

Steam's JPG and Special K's JXR/PNG of the same moment were listed as separate
captures: earlier pairing needed identical names and an HDR/SDR split. The scan
(backend.services.duplicate_service) applies the new matching to existing rows;
the server starts it in the background after this migration. Only
``capture_group_id`` changes, so every pairing can be undone with Ungroup.
"""

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("SELECT COUNT(*) FROM screenshots")
    total = (await cursor.fetchone())[0]
    if not total:
        return
    # Supersede any unfinished scan: this one starts over from the first row.
    await db.execute(
        """UPDATE maintenance_jobs SET status = 'completed', completed_at = datetime('now'),
               updated_at = datetime('now')
           WHERE kind = 'duplicate_backfill' AND status IN ('pending', 'running', 'failed')"""
    )
    await db.execute(
        "INSERT INTO maintenance_jobs (kind, total) VALUES ('duplicate_backfill', ?)",
        (total,),
    )
