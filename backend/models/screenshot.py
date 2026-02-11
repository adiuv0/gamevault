"""Pydantic models for screenshots."""

from pydantic import BaseModel


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
