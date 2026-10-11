"""Preserve in-flight uploads after a game is merged."""

async def upgrade(db):
    await db.execute("""CREATE TABLE game_redirects (
        source_id INTEGER PRIMARY KEY,
        target_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE
    )""")
