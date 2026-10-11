"""A random id for this GameVault server, so the sync app can tell servers apart.

The address alone can't: one server is often reachable under several names
(``tower``, its IP, a domain), and a rebuilt server can come back at the old
address with an empty library. The id lives in the database, so it survives
restarts and upgrades and travels with a restored backup.
"""

import logging
import secrets

from backend.config import settings
from backend.database import get_db

logger = logging.getLogger(__name__)

_KEY = "instance_id"
# Keyed by database path: tests (and a restore) can point the app at another file.
_cache: dict[str, str] = {}


async def get_instance_id() -> str | None:
    key = str(settings.db_path)
    if key not in _cache:
        try:
            db = await get_db()
            await db.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
                (_KEY, secrets.token_hex(8)),
            )
            await db.commit()
            cursor = await db.execute("SELECT value FROM app_settings WHERE key = ?", (_KEY,))
            row = await cursor.fetchone()
        except Exception:
            # The health check must answer even when the database is busy.
            logger.warning("couldn't read the server's instance id", exc_info=True)
            return None
        if row is None:
            return None
        _cache[key] = row[0]
    return _cache[key]
