"""Screenshot management routes."""

import mimetypes

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from backend.auth import require_auth
from backend.config import settings
from backend.models.annotation import AnnotationCreate
from backend.services import screenshot_service

router = APIRouter(
    prefix="/api/screenshots",
    tags=["screenshots"],
    dependencies=[Depends(require_auth)],
)


@router.get("/{screenshot_id}")
async def get_screenshot(screenshot_id: int):
    """Get screenshot details including annotation flag."""
    screenshot = await screenshot_service.get_screenshot(screenshot_id)
    if not screenshot:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return screenshot


@router.delete("/{screenshot_id}")
async def delete_screenshot(screenshot_id: int):
    """Delete a screenshot record and its files."""
    screenshot = await screenshot_service.get_screenshot(screenshot_id)
    if not screenshot:
        raise HTTPException(status_code=404, detail="Screenshot not found")

    # Delete files from disk
    for path_field in ["file_path", "thumbnail_path_sm", "thumbnail_path_md"]:
        rel_path = screenshot.get(path_field)
        if rel_path:
            full_path = settings.library_dir / rel_path
            if full_path.exists():
                full_path.unlink()

    await screenshot_service.delete_screenshot(screenshot_id)
    return {"message": "Screenshot deleted"}


@router.get("/{screenshot_id}/image")
async def get_image(screenshot_id: int):
    """Serve the full-resolution screenshot image."""
    screenshot = await screenshot_service.get_screenshot(screenshot_id)
    if not screenshot:
        raise HTTPException(status_code=404, detail="Screenshot not found")

    file_path = settings.library_dir / screenshot["file_path"]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Image file not found")

    media_type = mimetypes.guess_type(str(file_path))[0] or "image/jpeg"
    return FileResponse(file_path, media_type=media_type)


@router.get("/{screenshot_id}/thumb/{size}")
async def get_thumbnail(screenshot_id: int, size: str):
    """Serve a thumbnail. Size: sm (300px) or md (800px)."""
    if size not in ("sm", "md"):
        raise HTTPException(status_code=400, detail="Size must be 'sm' or 'md'")

    screenshot = await screenshot_service.get_screenshot(screenshot_id)
    if not screenshot:
        raise HTTPException(status_code=404, detail="Screenshot not found")

    path_field = "thumbnail_path_sm" if size == "sm" else "thumbnail_path_md"
    rel_path = screenshot.get(path_field)

    if not rel_path:
        rel_path = screenshot["file_path"]

    full_path = settings.library_dir / rel_path
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    return FileResponse(full_path, media_type="image/jpeg")


@router.post("/{screenshot_id}/favorite")
async def toggle_favorite(screenshot_id: int):
    """Toggle favorite status on a screenshot."""
    try:
        is_fav = await screenshot_service.toggle_favorite(screenshot_id)
        return {"is_favorite": is_fav}
    except ValueError:
        raise HTTPException(status_code=404, detail="Screenshot not found")


# ── Annotations ──────────────────────────────────────────────────────────────

@router.get("/{screenshot_id}/annotation")
async def get_annotation(screenshot_id: int):
    """Get annotation for a screenshot."""
    annotation = await screenshot_service.get_annotation(screenshot_id)
    if not annotation:
        raise HTTPException(status_code=404, detail="No annotation found")
    return annotation


@router.post("/{screenshot_id}/annotation")
async def save_annotation(screenshot_id: int, data: AnnotationCreate):
    """Create or update annotation for a screenshot."""
    screenshot = await screenshot_service.get_screenshot(screenshot_id)
    if not screenshot:
        raise HTTPException(status_code=404, detail="Screenshot not found")

    from markdown_it import MarkdownIt
    md = MarkdownIt()
    content_html = md.render(data.content)

    annotation = await screenshot_service.save_annotation(
        screenshot_id, data.content, content_html
    )
    return annotation


@router.delete("/{screenshot_id}/annotation")
async def delete_annotation(screenshot_id: int):
    """Delete annotation for a screenshot."""
    deleted = await screenshot_service.delete_annotation(screenshot_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="No annotation found")
    return {"message": "Annotation deleted"}
