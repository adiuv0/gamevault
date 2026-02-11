"""Pydantic models for share links."""

from pydantic import BaseModel


class ShareLinkCreate(BaseModel):
    expires_in: str | None = None  # e.g., "7d", "24h", None for no expiry


class ShareLinkResponse(BaseModel):
    id: int
    screenshot_id: int
    token: str
    url: str
    is_active: bool = True
    expires_at: str | None = None
    view_count: int = 0
    created_at: str
