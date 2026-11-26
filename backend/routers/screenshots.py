"""Screenshot management routes."""

import mimetypes

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# Some MIME types Python's mimetypes module doesn't know natively.
_EXTRA_MIME_TYPES = {
    ".jxr": "image/vnd.ms-photo",
    ".wdp": "image/vnd.ms-photo",
}

from backend.auth import require_auth
from backend.models.annotation import AnnotationCreate
from backend.models.screenshot import ScreenshotResponse
from backend.services import full_render_service, screenshot_delete_service, screenshot_service
from backend.services.screenshot_delete_service import MAX_BULK_DELETE
from backend.services.filesystem import (
    safe_library_path,
    safe_media_path,
)
from backend.services.media_cache_service import media_response_headers


class HashCheckRequest(BaseModel):
    hashes: list[str]
    # sha256 -> when the local file was taken (ISO 8601, local time), as GameVault
    # Sync works it out. Fills in the time of a stored copy that has none.
    capture_times: dict[str, str] | None = None

router = APIRouter(
    prefix="/api/screenshots",
    tags=["screenshots"],
    dependencies=[Depends(require_auth)],
)


@router.post("/check-hashes")
async def check_hashes(data: HashCheckRequest):
    """Check which sha256 hashes already exist in the database.

    Used by GameVault Sync to determine which local files need uploading.
    """
    existing = await screenshot_service.check_hashes_batch(data.hashes)
    all_set = set(data.hashes)
    dated = 0
    if data.capture_times:
        dated = await screenshot_service.apply_capture_time_hints(
            {sha: when for sha, when in data.capture_times.items() if sha in existing}
        )
    return {
        "existing": list(existing),
        "new": list(all_set - existing),
        "capture_times_applied": dated,
    }


@router.get("/{screenshot_id}", response_model=ScreenshotResponse)
async def get_screenshot(screenshot_id: int):
    """Get a logical capture and its physical variants for the viewer."""
    screenshot = await screenshot_service.get_capture_details(screenshot_id)
    if not screenshot:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return screenshot


@router.delete("/{screenshot_id}")
async def delete_screenshot(screenshot_id: int, variant: bool = False):
    """Delete a capture: every file in it, with thumbnails, previews and renders.

    ``?variant=true`` deletes only this file and keeps the capture's other files
    (for example drop the Steam JPG, keep the Special K JXR).
    """
    if variant:
        result = await screenshot_delete_service.delete_variant(screenshot_id)
    else:
        result = await screenshot_delete_service.delete_captures([screenshot_id])
        if not result["deleted_files"]:
            result = None
    if result is None:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return {"message": "Screenshot deleted", **result}


class BulkDeleteRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=MAX_BULK_DELETE)


@router.post("/delete")
async def delete_screenshots(data: BulkDeleteRequest):
    """Delete several captures at once (multi-select in the grid)."""
    return await screenshot_delete_service.delete_captures(data.ids)


@router.post("/{screenshot_id}/ungroup", response_model=ScreenshotResponse)
async def ungroup_screenshot(screenshot_id: int):
    screenshot = await screenshot_service.ungroup_screenshot(screenshot_id)
    if screenshot is None:
        raise HTTPException(404, "Screenshot not found")
    return screenshot


@router.get("/{screenshot_id}/image")
async def get_image(screenshot_id: int, v: str | None = None):
    """Serve the full-resolution screenshot image."""
    screenshot = await screenshot_service.get_screenshot_media(screenshot_id)
    if not screenshot:
        raise HTTPException(status_code=404, detail="Screenshot not found")

    file_path = safe_library_path(screenshot.get("file_path"))
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Image file not found")

    suffix = file_path.suffix.lower()
    media_type = (
        _EXTRA_MIME_TYPES.get(suffix)
        or mimetypes.guess_type(str(file_path))[0]
        or "image/jpeg"
    )
    return FileResponse(
        file_path,
        media_type=media_type,
        headers=media_response_headers(
            public=False,
            requested_version=v,
            current_version=screenshot["media_version"],
        ),
    )


@router.get("/{screenshot_id}/full")
async def get_full(screenshot_id: int, v: str | None = None):
    """Serve the full-resolution image in a format browsers can display.

    Browser-native originals are served as-is. JPEG XR and TIFF originals get
    a cached full-size JPEG render; if that can't be made, the preview is
    served instead so the viewer still shows something.
    """
    screenshot = await screenshot_service.get_screenshot_media(screenshot_id)
    if not screenshot:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    if not full_render_service.needs_full_render(screenshot):
        return await get_image(screenshot_id, v)

    render = await full_render_service.ensure_full_render(screenshot)
    if render is None:
        return await get_preview(screenshot_id, v)
    return FileResponse(
        render,
        media_type="image/jpeg",
        headers=media_response_headers(
            public=False,
            requested_version=v,
            current_version=screenshot["media_version"],
        ),
    )


@router.get("/{screenshot_id}/thumb/{size}")
async def get_thumbnail(screenshot_id: int, size: str, v: str | None = None):
    """Serve a thumbnail. Size: sm (300px) or md (800px)."""
    if size not in ("sm", "md"):
        raise HTTPException(status_code=400, detail="Size must be 'sm' or 'md'")

    screenshot = await screenshot_service.get_screenshot_media(screenshot_id)
    if not screenshot:
        raise HTTPException(status_code=404, detail="Screenshot not found")

    path_field = "thumbnail_path_sm" if size == "sm" else "thumbnail_path_md"
    rel_path = screenshot.get(path_field)

    if not rel_path:
        rel_path = screenshot.get("file_path")

    full_path = safe_media_path(rel_path)
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    return FileResponse(
        full_path,
        media_type="image/jpeg",
        headers=media_response_headers(
            public=False,
            requested_version=v,
            current_version=screenshot["media_version"],
        ),
    )


@router.get("/{screenshot_id}/preview")
async def get_preview(screenshot_id: int, v: str | None = None):
    """Serve a bounded browser preview, falling back while backfill runs."""
    screenshot = await screenshot_service.get_screenshot_media(screenshot_id)
    if not screenshot:
        raise HTTPException(status_code=404, detail="Screenshot not found")

    rel_path = None
    full_path = None
    for candidate in (
        screenshot.get("preview_path"),
        screenshot.get("thumbnail_path_md"),
        screenshot.get("file_path"),
    ):
        if not candidate:
            continue
        try:
            resolved = safe_media_path(candidate)
        except HTTPException:
            continue
        if resolved.exists():
            rel_path = candidate
            full_path = resolved
            break
    if rel_path is None or full_path is None:
        raise HTTPException(status_code=404, detail="Preview not found")

    is_derivative = rel_path != screenshot.get("file_path")
    media_type = "image/jpeg" if is_derivative else (
        _EXTRA_MIME_TYPES.get(full_path.suffix.lower())
        or mimetypes.guess_type(str(full_path))[0]
        or "image/jpeg"
    )
    return FileResponse(
        full_path,
        media_type=media_type,
        headers=media_response_headers(
            public=False,
            requested_version=v,
            current_version=screenshot["media_version"],
        ),
    )


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
    """Create or update annotation for a screenshot.

    Markdown is rendered with HTML disabled and the result is sanitized
    via nh3 before storage — both layers are required to prevent stored
    XSS in the authenticated UI and on public share pages.
    """
    screenshot = await screenshot_service.get_screenshot(screenshot_id)
    if not screenshot:
        raise HTTPException(status_code=404, detail="Screenshot not found")

    from backend.services.annotation_renderer import render_and_sanitize
    content_html = render_and_sanitize(data.content)

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
