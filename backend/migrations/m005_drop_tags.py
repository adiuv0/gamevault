"""Drop unused ``tags`` and ``screenshot_tags`` tables.

These were created in m001_initial.py as future-proofing but were never
wired up to a router, service, or UI. Leaving stale tables in the schema
is confusing for contributors and clutters introspection. If a tagging
feature is needed later, it can be added back via a new migration —
re-creating the tables takes seconds.
"""

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    # screenshot_tags has FKs into tags + screenshots; drop the join table
    # first so SQLite doesn't complain about the dangling FK constraint.
    await db.execute("DROP TABLE IF EXISTS screenshot_tags")
    await db.execute("DROP TABLE IF EXISTS tags")
    await db.commit()
