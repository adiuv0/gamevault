"""Pydantic models for games."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.services.media_cache_service import media_version


class GameCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    steam_app_id: int | None = None


class GameUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
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
    # "#rrggbb" taken from the cover; None or "" when there is none.
    accent_color: str | None = None
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
    # Opaque cover revision: present only when the game has a cover, and
    # changes when the game is updated, so replaced covers aren't served stale.
    cover_version: str | None = None
    accent_color: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_cover_version(cls, data: Any) -> Any:
        if isinstance(data, dict) and "cover_version" not in data:
            if data.get("cover_image_path"):
                data = {**data, "cover_version": media_version(str(data.get("updated_at") or ""))}
        return data


class PublicGameListResponse(BaseModel):
    games: list[PublicGame]
    total: int
