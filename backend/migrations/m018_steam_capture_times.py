"""Keep the time Steam posted a screenshot apart from the time it was taken.

Steam imports stored Steam's posted time as the capture time. Steam posts a play
session, or several days of them, as one batch a couple of seconds apart, so
those captures sat on the upload day in upload order. The real time is in the
file name Steam keeps for each upload.

``steam_posted_at`` holds what Steam reported. NULL marks a Steam import whose
capture time has not been looked up yet; an empty string means it was looked up
and Steam gave no posted time. Looking it up needs Steam, so it happens in the
background after startup (backend.services.steam_capture_times) and whenever a
Steam import meets a screenshot it already has.
"""

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    await db.execute("ALTER TABLE screenshots ADD COLUMN steam_posted_at TEXT")
    cursor = await db.execute(
        """SELECT COUNT(*) FROM screenshots
           WHERE source = 'steam_import' AND steam_screenshot_id IS NOT NULL"""
    )
    total = (await cursor.fetchone())[0]
    if total:
        await db.execute(
            "INSERT INTO maintenance_jobs (kind, total) VALUES ('steam_capture_times', ?)",
            (total,),
        )
