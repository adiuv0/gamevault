"""Pydantic models for annotations."""

from pydantic import BaseModel, Field


class AnnotationCreate(BaseModel):
    content: str = Field(..., min_length=1)


class AnnotationResponse(BaseModel):
    id: int
    screenshot_id: int
    content: str
    content_html: str | None = None
    created_at: str
    updated_at: str
