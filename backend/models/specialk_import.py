"""Pydantic models for Special K filesystem import."""

from pydantic import BaseModel, Field


class SpecialKScanRequest(BaseModel):
    path: str = Field(..., min_length=1)


class SpecialKScanGame(BaseModel):
    folder_name: str
    suggested_name: str
    screenshot_count: int
    has_hdr: bool
    has_sdr: bool


class SpecialKScanResponse(BaseModel):
    valid: bool
    path: str
    total_games: int = 0
    total_screenshots: int = 0
    games: list[SpecialKScanGame] = Field(default_factory=list)
    error: str | None = None


class SpecialKImportRequest(BaseModel):
    path: str = Field(..., min_length=1)
    folder_names: list[str] = Field(
        default_factory=list,
        description="Empty = import all detected games",
    )


class SpecialKImportSessionResponse(BaseModel):
    id: int
    scan_path: str
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
