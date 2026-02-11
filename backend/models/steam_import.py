"""Pydantic models for Steam import."""

from pydantic import BaseModel, Field


class SteamValidateRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    steam_login_secure: str = Field(default="")
    session_id: str = Field(default="")


class SteamValidateResponse(BaseModel):
    valid: bool
    profile_name: str | None = None
    avatar_url: str | None = None
    is_numeric_id: bool = False
    error: str | None = None


class SteamImportRequest(BaseModel):
    user_id: str
    steam_login_secure: str = ""
    session_id: str = ""
    game_ids: list[int] = Field(default_factory=list, description="Empty = import all games")
    is_numeric_id: bool = False


class SteamGameInfo(BaseModel):
    app_id: int
    name: str
    screenshot_count: int


class SteamImportSessionResponse(BaseModel):
    id: int
    steam_user_id: str
    status: str
    total_games: int
    completed_games: int
    total_screenshots: int
    completed_screenshots: int
    skipped_screenshots: int
    failed_screenshots: int
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str
