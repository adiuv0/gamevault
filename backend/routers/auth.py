"""Authentication routes: login, setup, verify, change password."""

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.auth import (
    create_access_token,
    get_password_hash,
    require_auth,
    set_password,
    verify_password,
)
from backend.config import settings

# ── Rate limiting ────────────────────────────────────────────────────────────
# Track failed attempts per IP: {ip: [timestamp, ...]}. Login and password
# change have separate counters so a logged-in attacker can't bypass login
# rate limiting by switching to change-password.
_login_attempts: dict[str, list[float]] = {}
_change_password_attempts: dict[str, list[float]] = {}
_RATE_LIMIT_WINDOW = 15 * 60  # 15 minutes
_RATE_LIMIT_MAX = 5  # max failures before lockout


def _check_rate_limit(
    bucket: dict[str, list[float]], ip: str, kind: str
) -> None:
    """Raise 429 if this IP has too many recent failures in ``bucket``."""
    now = time.monotonic()
    attempts = bucket.get(ip, [])
    # Prune old entries
    attempts = [t for t in attempts if now - t < _RATE_LIMIT_WINDOW]
    bucket[ip] = attempts
    if len(attempts) >= _RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed {kind} attempts. Try again later.",
        )


def _record_failed_attempt(bucket: dict[str, list[float]], ip: str) -> None:
    bucket.setdefault(ip, []).append(time.monotonic())


def _clear_attempts(bucket: dict[str, list[float]], ip: str) -> None:
    bucket.pop(ip, None)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str = Field(..., min_length=1)


class SetupRequest(BaseModel):
    password: str = Field(..., min_length=6)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=6)


class AuthResponse(BaseModel):
    token: str
    expires_in_days: int


class AuthStatus(BaseModel):
    authenticated: bool
    setup_required: bool
    auth_disabled: bool


@router.post("/login", response_model=AuthResponse)
async def login(req: LoginRequest, request: Request):
    """Authenticate with password and receive a JWT token."""
    if settings.disable_auth:
        return AuthResponse(
            token=create_access_token(),
            expires_in_days=settings.token_expiry_days,
        )

    client_ip = _client_ip(request)
    _check_rate_limit(_login_attempts, client_ip, "login")

    stored_hash = await get_password_hash()
    if stored_hash is None:
        raise HTTPException(status_code=403, detail="setup_required")

    if not verify_password(req.password, stored_hash):
        _record_failed_attempt(_login_attempts, client_ip)
        raise HTTPException(status_code=401, detail="Invalid password")

    _clear_attempts(_login_attempts, client_ip)
    return AuthResponse(
        token=create_access_token(),
        expires_in_days=settings.token_expiry_days,
    )


@router.post("/setup", response_model=AuthResponse)
async def setup(req: SetupRequest):
    """First-run password setup. Only works if no password is set yet."""
    if settings.disable_auth:
        raise HTTPException(status_code=400, detail="Auth is disabled")

    stored_hash = await get_password_hash()
    if stored_hash is not None:
        raise HTTPException(status_code=400, detail="Password already set")

    await set_password(req.password)

    return AuthResponse(
        token=create_access_token(),
        expires_in_days=settings.token_expiry_days,
    )


@router.get("/verify", response_model=AuthStatus)
async def verify():
    """Check authentication status.

    Returns whether auth is disabled, setup is needed, or user is authenticated.
    This endpoint is always accessible (no auth required).
    """
    if settings.disable_auth:
        return AuthStatus(authenticated=True, setup_required=False, auth_disabled=True)

    stored_hash = await get_password_hash()
    if stored_hash is None:
        return AuthStatus(authenticated=False, setup_required=True, auth_disabled=False)

    return AuthStatus(authenticated=False, setup_required=False, auth_disabled=False)


@router.post("/change-password", dependencies=[Depends(require_auth)])
async def change_password(req: ChangePasswordRequest, request: Request):
    """Change the password.

    Requires a valid JWT (``Depends(require_auth)``) AND the current
    password. Failed current-password verifications are rate-limited per
    IP, separately from login, so a logged-in attacker can't brute-force
    the password through this endpoint.
    """
    if settings.disable_auth:
        raise HTTPException(status_code=400, detail="Auth is disabled")

    client_ip = _client_ip(request)
    _check_rate_limit(_change_password_attempts, client_ip, "password change")

    stored_hash = await get_password_hash()
    if stored_hash is None:
        raise HTTPException(status_code=400, detail="No password set. Use /setup instead.")

    if not verify_password(req.current_password, stored_hash):
        _record_failed_attempt(_change_password_attempts, client_ip)
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    _clear_attempts(_change_password_attempts, client_ip)
    await set_password(req.new_password)
    return {"message": "Password changed successfully"}
