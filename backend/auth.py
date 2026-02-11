"""Authentication: single-password auth with JWT tokens."""

from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, Request
from jose import JWTError, jwt

from backend.config import settings
from backend.database import get_db

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a password against its hash."""
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(expires_days: int | None = None) -> str:
    """Create a JWT access token."""
    if expires_days is None:
        expires_days = settings.token_expiry_days
    expire = datetime.now(timezone.utc) + timedelta(days=expires_days)
    payload = {
        "sub": "gamevault_user",
        "exp": expire,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token. Raises JWTError on failure."""
    return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])


async def get_password_hash() -> str | None:
    """Get the stored password hash from app_settings, or None if not set."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT value FROM app_settings WHERE key = 'auth_password_hash'"
    )
    row = await cursor.fetchone()
    return row[0] if row else None


async def set_password(password: str) -> None:
    """Store a hashed password in app_settings."""
    db = await get_db()
    hashed = hash_password(password)
    await db.execute(
        """INSERT INTO app_settings (key, value) VALUES ('auth_password_hash', ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (hashed,),
    )
    await db.commit()


async def require_auth(request: Request) -> dict | None:
    """FastAPI dependency that enforces authentication.

    Skips auth for:
    - Public share endpoints (/share/*)
    - Health check (/api/health)
    - Auth endpoints (/api/auth/*)
    - When GAMEVAULT_DISABLE_AUTH=true
    """
    path = request.url.path

    # Public endpoints that never require auth
    if path.startswith("/share/") or path == "/api/health" or path.startswith("/api/auth/"):
        return None

    # Auth disabled mode
    if settings.disable_auth:
        return {"sub": "gamevault_user"}

    # Check for setup state (no password set yet)
    stored_hash = await get_password_hash()
    if stored_hash is None:
        # First run — allow access to setup endpoint only
        if path == "/api/auth/setup":
            return None
        # For other endpoints, signal that setup is needed
        raise HTTPException(
            status_code=403,
            detail="setup_required",
        )

    # Extract and validate token
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authentication token")

    token = auth_header.split(" ", 1)[1]
    try:
        payload = decode_token(token)
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
