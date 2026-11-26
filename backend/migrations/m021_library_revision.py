"""Keep a library revision number so Timeline statistics can be cached safely.

Statistics over a whole library read every capture, so the server caches them and
needs to know when any capture, game or annotation changed. Requests use separate
pooled connections, so a per-connection change counter cannot see other requests'
writes; triggers bump one shared row instead. Older releases ignore the table, and
the triggers keep working under them because the table stays.
"""

import aiosqlite

_WATCHED = ("screenshots", "games", "annotations")


async def upgrade(db: aiosqlite.Connection) -> None:
    await db.execute(
        """CREATE TABLE library_revision (
               id INTEGER PRIMARY KEY CHECK (id = 1),
               token TEXT NOT NULL,
               value INTEGER NOT NULL
           )"""
    )
    # The token tells two databases apart (a fresh library, a restored backup) that
    # happen to sit at the same revision number.
    await db.execute(
        "INSERT INTO library_revision (id, token, value) VALUES (1, lower(hex(randomblob(8))), 0)"
    )
    for table in _WATCHED:
        for event in ("INSERT", "UPDATE", "DELETE"):
            await db.execute(
                f"""CREATE TRIGGER {table}_library_revision_{event.lower()}
                    AFTER {event} ON {table} BEGIN
                        UPDATE library_revision SET value = value + 1 WHERE id = 1;
                    END"""
            )
