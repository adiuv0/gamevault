"""Authentication routes: login, setup, verify, change password."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.auth import (
    create_access_token,
    get_password_hash,
    set_password,
    verify_password,
)
from backend.config import settings

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
async def login(req: LoginRequest):
    """Authenticate with password and receive a JWT token."""
    if settings.disable_auth:
        return AuthResponse(
            token=create_access_token(),
            expires_in_days=settings.token_expiry_days,
        )

    stored_hash = await get_password_hash()
    if stored_hash is None:
        raise HTTPException(status_code=403, detail="setup_required")

    if not verify_password(req.password, stored_hash):
        raise HTTPException(status_code=401, detail="Invalid password")

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


@router.post("/change-password")
async def change_password(req: ChangePasswordRequest):
    """Change the password. Requires current password for verification."""
    if settings.disable_auth:
        raise HTTPException(status_code=400, detail="Auth is disabled")

    stored_hash = await get_password_hash()
    if stored_hash is None:
        raise HTTPException(status_code=400, detail="No password set. Use /setup instead.")

    if not verify_password(req.current_password, stored_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    await set_password(req.new_password)
    return {"message": "Password changed successfully"}
