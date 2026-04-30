"""Pydantic models for screenshots."""

from pydantic import BaseModel, ConfigDict


class ScreenshotResponse(BaseModel):
    id: int
    game_id: int
    filename: str
    file_path: str
    thumbnail_path_sm: str | None = None
    thumbnail_path_md: str | None = None
    file_size: int | None = None
    width: int | None = None
    height: int | None = None
    format: str | None = None
    taken_at: str | None = None
    uploaded_at: str
    steam_screenshot_id: str | None = None
    steam_description: str | None = None
    source: str = "upload"
    is_favorite: bool = False
    view_count: int = 0
    exif_data: str | None = None
    has_annotation: bool = False
    created_at: str
    updated_at: str


class ScreenshotListResponse(BaseModel):
    screenshots: list[ScreenshotResponse]
    total: int
    page: int
    limit: int
    has_more: bool


class ScreenshotUpdate(BaseModel):
    taken_at: str | None = None
    is_favorite: bool | None = None


# ── Public-gallery response shapes ────────────────────────────────────────────
# These project away internal fields (file_path, sha256_hash, exif_data,
# steam_screenshot_id, source, ...) so the unauthenticated /api/gallery
# endpoints don't leak them. Use as ``response_model=`` on those routes;
# FastAPI then drops anything not declared here when serializing.


class PublicScreenshot(BaseModel):
    """Slim screenshot view for unauthenticated /api/gallery responses."""

    model_config = ConfigDict(extra="ignore")

    id: int
    game_id: int
    filename: str
    thumbnail_path_sm: str | None = None
    thumbnail_path_md: str | None = None
    width: int | None = None
    height: int | None = None
    taken_at: str | None = None
    uploaded_at: str


class PublicScreenshotListResponse(BaseModel):
    screenshots: list[PublicScreenshot]
    total: int
    page: int
    limit: int
    has_more: bool
