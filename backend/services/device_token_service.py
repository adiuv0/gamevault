"""Device tokens: long-lived sign-ins for the GameVault Sync desktop app.

A browser session lasts ``token_expiry_days``; a sync app that stops working
every month is not "set and forget". Each paired PC instead gets its own random
token, listed in Settings, revocable on its own, and cleared when the password
changes. Device tokens can only reach the endpoints the sync app uses.
"""

import hashlib
import re
import secrets

from backend.database import get_db

TOKEN_PREFIX = "gvd_"

# (method, path) pairs a device token may call. Everything else is refused, so a
# token copied off a gaming PC can add screenshots but not read or delete them.
_DEVICE_ALLOWED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GET", re.compile(r"^/api/auth/verify$")),
    ("POST", re.compile(r"^/api/screenshots/check-hashes$")),
    ("POST", re.compile(r"^/api/games/by-name$")),
    ("GET", re.compile(r"^/api/games/by-steam-appid/\d+$")),
    ("POST", re.compile(r"^/api/upload/sync$")),
    ("DELETE", re.compile(r"^/api/auth/devices/current$")),
)


def is_device_token(token: str) -> bool:
    return token.startswith(TOKEN_PREFIX)


def device_may_access(method: str, path: str) -> bool:
    return any(method == m and pattern.match(path) for m, pattern in _DEVICE_ALLOWED)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _clean_name(name: str) -> str:
    name = " ".join(name.split())[:80]
    return name or "Unnamed device"


async def create_device_token(name: str, *, install_id: str | None = None) -> tuple[str, dict]:
    """Create a token and return ``(plaintext, record)``; the plaintext is never stored.

    An install that signs in again (same ``install_id``) takes over its old
    entry instead of piling up duplicates in Settings. Matching on the id rather
    than the name keeps two PCs that happen to share a name from signing each
    other out.
    """
    name = _clean_name(name)
    install_id = (install_id or "").strip() or None
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    db = await get_db()
    if install_id:
        await db.execute("DELETE FROM device_tokens WHERE install_id = ?", (install_id,))
    cursor = await db.execute(
        "INSERT INTO device_tokens (name, install_id, token_hash) VALUES (?, ?, ?)",
        (name, install_id, _hash(token)),
    )
    device_id = cursor.lastrowid
    await db.commit()
    return token, await get_device(device_id)


async def get_device(device_id: int) -> dict | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, name, created_at, last_used_at FROM device_tokens WHERE id = ?",
        (device_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "created_at": row[2], "last_used_at": row[3]}


async def list_devices() -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, name, created_at, last_used_at FROM device_tokens
           ORDER BY COALESCE(last_used_at, created_at) DESC, id DESC"""
    )
    return [
        {"id": r[0], "name": r[1], "created_at": r[2], "last_used_at": r[3]}
        for r in await cursor.fetchall()
    ]


async def revoke_device(device_id: int) -> bool:
    db = await get_db()
    cursor = await db.execute("DELETE FROM device_tokens WHERE id = ?", (device_id,))
    await db.commit()
    return cursor.rowcount > 0


async def revoke_all_devices() -> None:
    db = await get_db()
    await db.execute("DELETE FROM device_tokens")


async def authenticate(token: str) -> dict | None:
    """Return the device for a valid token, recording when it was last seen."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, name,
                  last_used_at IS NULL OR last_used_at < datetime('now', '-5 minutes')
           FROM device_tokens WHERE token_hash = ?""",
        (_hash(token),),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    # Throttled so a sync burst doesn't turn every request into a write. Decide
    # before writing: an UPDATE matching nothing still opens a transaction.
    if row[2]:
        await db.execute(
            "UPDATE device_tokens SET last_used_at = datetime('now') WHERE id = ?", (row[0],)
        )
        await db.commit()
    return {"id": row[0], "name": row[1]}
