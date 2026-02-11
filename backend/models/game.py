"""Pydantic models for games."""

from pydantic import BaseModel, Field


class GameCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    steam_app_id: int | None = None


class GameUpdate(BaseModel):
    name: str | None = None
    steam_app_id: int | None = None
    developer: str | None = None
    publisher: str | None = None
    release_date: str | None = None
    genres: str | None = None
    description: str | None = None
    is_public: bool | None = None


class GameResponse(BaseModel):
    id: int
    name: str
    folder_name: str
    steam_app_id: int | None = None
    cover_image_path: str | None = None
    developer: str | None = None
    publisher: str | None = None
    release_date: str | None = None
    genres: str | None = None
    description: str | None = None
    is_public: bool = True
    screenshot_count: int = 0
    first_screenshot_date: str | None = None
    last_screenshot_date: str | None = None
    created_at: str
    updated_at: str


class GameListResponse(BaseModel):
    games: list[GameResponse]
    total: int
