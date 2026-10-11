"""Long-lived, revocable sign-ins for the GameVault Sync desktop app.

Only a SHA-256 of each token is stored. Tokens are random 256-bit values, so a
fast hash is enough; the plaintext is shown once, to the device that asked.
``install_id`` is a random id the app keeps per install, so signing in again
replaces that install's old token without touching another PC with the same name.
"""


async def upgrade(db):
    await db.execute(
        """CREATE TABLE device_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            install_id TEXT,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_used_at TEXT
        )"""
    )
    await db.execute(
        "CREATE INDEX idx_device_tokens_install ON device_tokens(install_id) WHERE install_id IS NOT NULL"
    )
