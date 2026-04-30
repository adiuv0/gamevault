"""Pydantic models for games."""

from pydantic import BaseModel, ConfigDict, Field


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


# ── Public-gallery response shapes ────────────────────────────────────────────


class PublicGame(BaseModel):
    """Slim game view for unauthenticated /api/gallery responses.

    Excludes internal fields like ``folder_name`` (filesystem layout),
    ``cover_image_path`` (the gallery uses /api/gallery/games/{id}/cover
    instead), and timestamps that aren't user-facing.
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    steam_app_id: int | None = None
    developer: str | None = None
    publisher: str | None = None
    release_date: str | None = None
    genres: str | None = None
    description: str | None = None
    screenshot_count: int = 0
    first_screenshot_date: str | None = None
    last_screenshot_date: str | None = None


class PublicGameListResponse(BaseModel):
    games: list[PublicGame]
    total: int
