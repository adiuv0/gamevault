"""Add durable browser-upload jobs and per-file replay state."""

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    await db.execute(
        """CREATE TABLE upload_jobs (
               task_id          TEXT PRIMARY KEY,
               game_id          INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
               taken_at         TEXT,
               status           TEXT NOT NULL DEFAULT 'pending'
                                CHECK(status IN (
                                    'pending', 'running', 'completed', 'failed', 'cancelled'
                                )),
               total_files      INTEGER NOT NULL DEFAULT 0,
               completed_files  INTEGER NOT NULL DEFAULT 0,
               skipped_files    INTEGER NOT NULL DEFAULT 0,
               failed_files     INTEGER NOT NULL DEFAULT 0,
               attempts         INTEGER NOT NULL DEFAULT 0,
               error_message    TEXT,
               created_at       TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
               started_at       TEXT,
               completed_at     TEXT
           )"""
    )
    await db.execute(
        """CREATE TABLE upload_job_files (
               task_id          TEXT NOT NULL REFERENCES upload_jobs(task_id)
                                ON DELETE CASCADE,
               file_index       INTEGER NOT NULL,
               display_name     TEXT NOT NULL,
               storage_name     TEXT NOT NULL,
               size_bytes       INTEGER NOT NULL,
               status           TEXT NOT NULL DEFAULT 'pending'
                                CHECK(status IN (
                                    'pending', 'processing', 'completed', 'skipped', 'failed'
                                )),
               target_filename  TEXT,
               screenshot_id    INTEGER REFERENCES screenshots(id) ON DELETE SET NULL,
               error_message    TEXT,
               updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
               PRIMARY KEY (task_id, file_index),
               UNIQUE (task_id, storage_name)
           )"""
    )
    await db.execute(
        """CREATE INDEX idx_upload_jobs_status_created
           ON upload_jobs(status, created_at, task_id)"""
    )
    await db.commit()
