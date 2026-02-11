"""Pydantic models for search."""

from pydantic import BaseModel


class SearchQuery(BaseModel):
    q: str
    game_id: int | None = None
    date_from: str | None = None
    date_to: str | None = None
    sort: str = "relevance"  # relevance | date
    page: int = 1
    limit: int = 50


class SearchResult(BaseModel):
    screenshot_id: int
    game_id: int
    game_name: str
    filename: str
    thumbnail_url: str | None = None
    taken_at: str | None = None
    annotation_preview: str | None = None
    relevance_score: float | None = None
