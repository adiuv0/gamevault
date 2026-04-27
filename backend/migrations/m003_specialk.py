"""Add specialk_import_sessions table for tracking local Special K imports."""

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    await db.execute("""
        CREATE TABLE specialk_import_sessions (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_path               TEXT NOT NULL,
            status                  TEXT NOT NULL DEFAULT 'pending',
            total_games             INTEGER DEFAULT 0,
            completed_games         INTEGER DEFAULT 0,
            total_screenshots       INTEGER DEFAULT 0,
            completed_screenshots   INTEGER DEFAULT 0,
            skipped_screenshots     INTEGER DEFAULT 0,
            failed_screenshots      INTEGER DEFAULT 0,
            error_log               TEXT,
            started_at              TEXT,
            completed_at            TEXT,
            created_at              TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    await db.commit()
