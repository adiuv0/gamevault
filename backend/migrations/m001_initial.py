"""Initial database schema.

Creates all core tables, indexes, FTS5 virtual table, and triggers.
"""

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    """Create the initial database schema."""

    # ── Games table ──────────────────────────────────────────────────────
    await db.execute("""
        CREATE TABLE games (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            name                  TEXT NOT NULL UNIQUE,
            folder_name           TEXT NOT NULL UNIQUE,
            steam_app_id          INTEGER,
            cover_image_path      TEXT,
            header_image_url      TEXT,
            developer             TEXT,
            publisher             TEXT,
            release_date          TEXT,
            genres                TEXT,
            description           TEXT,
            igdb_id               INTEGER,
            steamgriddb_id        INTEGER,
            screenshot_count      INTEGER DEFAULT 0,
            first_screenshot_date TEXT,
            last_screenshot_date  TEXT,
            created_at            TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    await db.execute("CREATE INDEX idx_games_steam_app_id ON games(steam_app_id)")
    await db.execute("CREATE INDEX idx_games_name ON games(name)")

    # ── Screenshots table ────────────────────────────────────────────────
    await db.execute("""
        CREATE TABLE screenshots (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id             INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            filename            TEXT NOT NULL,
            file_path           TEXT NOT NULL,
            thumbnail_path_sm   TEXT,
            thumbnail_path_md   TEXT,
            file_size           INTEGER,
            width               INTEGER,
            height              INTEGER,
            format              TEXT,
            taken_at            TEXT,
            uploaded_at         TEXT NOT NULL DEFAULT (datetime('now')),
            steam_screenshot_id TEXT,
            steam_description   TEXT,
            source              TEXT NOT NULL DEFAULT 'upload',
            is_favorite         INTEGER DEFAULT 0,
            view_count          INTEGER DEFAULT 0,
            sha256_hash         TEXT,
            exif_data           TEXT,
            created_at          TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    await db.execute("CREATE INDEX idx_screenshots_game_id ON screenshots(game_id)")
    await db.execute("CREATE INDEX idx_screenshots_taken_at ON screenshots(taken_at)")
    await db.execute(
        "CREATE INDEX idx_screenshots_steam_id ON screenshots(steam_screenshot_id)"
    )
    await db.execute("CREATE INDEX idx_screenshots_hash ON screenshots(sha256_hash)")
    await db.execute("CREATE INDEX idx_screenshots_source ON screenshots(source)")

    # ── Annotations table ────────────────────────────────────────────────
    await db.execute("""
        CREATE TABLE annotations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            screenshot_id   INTEGER NOT NULL REFERENCES screenshots(id) ON DELETE CASCADE,
            content         TEXT NOT NULL,
            content_html    TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    await db.execute("CREATE INDEX idx_annotations_screenshot_id ON annotations(screenshot_id)")

    # ── Share links table ────────────────────────────────────────────────
    await db.execute("""
        CREATE TABLE share_links (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            screenshot_id   INTEGER NOT NULL REFERENCES screenshots(id) ON DELETE CASCADE,
            token           TEXT NOT NULL UNIQUE,
            is_active       INTEGER DEFAULT 1,
            expires_at      TEXT,
            view_count      INTEGER DEFAULT 0,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    await db.execute("CREATE INDEX idx_share_links_token ON share_links(token)")
    await db.execute(
        "CREATE INDEX idx_share_links_screenshot_id ON share_links(screenshot_id)"
    )

    # ── Steam import sessions table ──────────────────────────────────────
    await db.execute("""
        CREATE TABLE steam_import_sessions (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            steam_user_id           TEXT NOT NULL,
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

    # ── Tags tables (future-proofing) ────────────────────────────────────
    await db.execute("""
        CREATE TABLE tags (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            name    TEXT NOT NULL UNIQUE
        )
    """)
    await db.execute("""
        CREATE TABLE screenshot_tags (
            screenshot_id INTEGER NOT NULL REFERENCES screenshots(id) ON DELETE CASCADE,
            tag_id        INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY (screenshot_id, tag_id)
        )
    """)

    # ── App settings table ───────────────────────────────────────────────
    await db.execute("""
        CREATE TABLE app_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # ── FTS5 full-text search ────────────────────────────────────────────

    # Content table that mirrors searchable fields
    await db.execute("""
        CREATE TABLE screenshots_fts_content (
            rowid              INTEGER PRIMARY KEY,
            game_name          TEXT,
            filename           TEXT,
            steam_description  TEXT,
            annotation_content TEXT
        )
    """)

    # FTS5 virtual table using external content
    await db.execute("""
        CREATE VIRTUAL TABLE screenshots_fts USING fts5(
            game_name,
            filename,
            steam_description,
            annotation_content,
            content=screenshots_fts_content,
            content_rowid=rowid,
            tokenize='porter unicode61'
        )
    """)

    # Triggers to keep FTS in sync with content table
    await db.execute("""
        CREATE TRIGGER screenshots_fts_ai AFTER INSERT ON screenshots_fts_content BEGIN
            INSERT INTO screenshots_fts(rowid, game_name, filename, steam_description, annotation_content)
            VALUES (new.rowid, new.game_name, new.filename, new.steam_description, new.annotation_content);
        END
    """)

    await db.execute("""
        CREATE TRIGGER screenshots_fts_ad AFTER DELETE ON screenshots_fts_content BEGIN
            INSERT INTO screenshots_fts(screenshots_fts, rowid, game_name, filename, steam_description, annotation_content)
            VALUES ('delete', old.rowid, old.game_name, old.filename, old.steam_description, old.annotation_content);
        END
    """)

    await db.execute("""
        CREATE TRIGGER screenshots_fts_au AFTER UPDATE ON screenshots_fts_content BEGIN
            INSERT INTO screenshots_fts(screenshots_fts, rowid, game_name, filename, steam_description, annotation_content)
            VALUES ('delete', old.rowid, old.game_name, old.filename, old.steam_description, old.annotation_content);
            INSERT INTO screenshots_fts(rowid, game_name, filename, steam_description, annotation_content)
            VALUES (new.rowid, new.game_name, new.filename, new.steam_description, new.annotation_content);
        END
    """)

    await db.commit()
