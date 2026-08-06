"""Persist resumable maintenance jobs and duplicate-review candidates."""

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    await db.execute(
        """CREATE TABLE maintenance_jobs (
               id            INTEGER PRIMARY KEY AUTOINCREMENT,
               kind          TEXT NOT NULL,
               status        TEXT NOT NULL DEFAULT 'pending'
                             CHECK(status IN ('pending', 'running', 'completed', 'failed')),
               cursor_id     INTEGER NOT NULL DEFAULT 0,
               total         INTEGER NOT NULL DEFAULT 0,
               processed     INTEGER NOT NULL DEFAULT 0,
               errors        INTEGER NOT NULL DEFAULT 0,
               error_message TEXT,
               created_at    TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
               completed_at  TEXT
           )"""
    )
    await db.execute(
        """CREATE INDEX idx_maintenance_jobs_kind_status
           ON maintenance_jobs(kind, status, id DESC)"""
    )

    await db.execute(
        """CREATE TABLE duplicate_candidates (
               id                  INTEGER PRIMARY KEY AUTOINCREMENT,
               game_id             INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
               left_screenshot_id  INTEGER NOT NULL REFERENCES screenshots(id) ON DELETE CASCADE,
               right_screenshot_id INTEGER NOT NULL REFERENCES screenshots(id) ON DELETE CASCADE,
               hash_distance       INTEGER NOT NULL,
               confidence          TEXT NOT NULL CHECK(confidence IN ('likely', 'possible')),
               reason              TEXT NOT NULL,
               status              TEXT NOT NULL DEFAULT 'pending'
                                   CHECK(status IN ('pending', 'grouped', 'dismissed')),
               created_at          TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
               UNIQUE(left_screenshot_id, right_screenshot_id),
               CHECK(left_screenshot_id < right_screenshot_id)
           )"""
    )
    await db.execute(
        """CREATE INDEX idx_duplicate_candidates_status_id
           ON duplicate_candidates(status, id DESC)"""
    )
    await db.execute(
        """CREATE INDEX idx_duplicate_candidates_game_status
           ON duplicate_candidates(game_id, status, id DESC)"""
    )
    await db.execute(
        """CREATE INDEX idx_screenshots_duplicate_neighborhood
           ON screenshots(game_id, width, height, taken_at, id)"""
    )
