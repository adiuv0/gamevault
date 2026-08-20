"""Add an encrypted durable queue for restart-safe filesystem imports."""

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    await db.execute(
        """CREATE TABLE import_jobs (
               id                INTEGER PRIMARY KEY AUTOINCREMENT,
               kind              TEXT NOT NULL CHECK(kind IN ('steam', 'specialk')),
               session_id        INTEGER NOT NULL,
               payload_encrypted TEXT,
               status            TEXT NOT NULL DEFAULT 'pending'
                                 CHECK(status IN (
                                     'pending', 'running', 'completed', 'failed', 'cancelled'
                                 )),
               attempts          INTEGER NOT NULL DEFAULT 0,
               error_message     TEXT,
               created_at        TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
               started_at        TEXT,
               completed_at      TEXT,
               UNIQUE(kind, session_id)
           )"""
    )
    await db.execute(
        """CREATE INDEX idx_import_jobs_status_id
           ON import_jobs(status, id)"""
    )
    await db.commit()
