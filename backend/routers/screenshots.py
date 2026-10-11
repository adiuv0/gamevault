"""Screenshot management routes."""

import asyncio
import mimetypes

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

# Some MIME types Python's mimetypes module doesn't know natively.
_EXTRA_MIME_TYPES = {
    ".jxr": "image/vnd.ms-photo",
    ".wdp": "image/vnd.ms-photo",
}

from backend.auth import require_auth
from backend.database import get_db
from backend.models.annotation import AnnotationCreate
from backend.models.screenshot import ScreenshotResponse
from backend.services import screenshot_service
from backend.services.filesystem import safe_library_path, safe_media_path
from backend.services.media_cache_service import media_response_headers


class HashCheckRequest(BaseModel):
    hashes: list[str]

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
    return {"existing": list(existing), "new": list(all_set - existing)}


@router.get("/{screenshot_id}", response_model=ScreenshotResponse)
async def get_screenshot(screenshot_id: int):
    """Get a logical capture and its physical variants for the viewer."""
    screenshot = await screenshot_service.get_capture_details(screenshot_id)
    if not screenshot:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return screenshot


@router.delete("/{screenshot_id}")
async def delete_screenshot(screenshot_id: int):
    """Delete a screenshot record and its files."""
    screenshot = await screenshot_service.get_screenshot(screenshot_id)
    if not screenshot:
        raise HTTPException(status_code=404, detail="Screenshot not found")

    # Resolve every file first — through the library-containment check so a
    # poisoned DB row can't reach outside — and skip any still shared by
    # another row.
    doomed = []
    for path_field in [
        "file_path",
        "thumbnail_path_sm",
        "thumbnail_path_md",
        "preview_path",
    ]:
        rel_path = screenshot.get(path_field)
        if not rel_path:
            continue
        db = await get_db()
        cursor = await db.execute(
            """SELECT 1 FROM screenshots WHERE id != ? AND (
                file_path = ? OR thumbnail_path_sm = ? OR thumbnail_path_md = ? OR preview_path = ?
            ) LIMIT 1""",
            (screenshot_id, rel_path, rel_path, rel_path, rel_path),
        )
        if await cursor.fetchone():
            continue
        try:
            resolver = safe_library_path if path_field == "file_path" else safe_media_path
            doomed.append(resolver(rel_path))
        except HTTPException:
            # Unsafe stored path — skip the unlink rather than fail the
            # whole delete. The DB row will still be removed below.
            continue

    # Remove the row before the files: if the delete fails, nothing points at
    # missing originals.
    await screenshot_service.delete_screenshot(screenshot_id)

    def unlink_all():
        for full_path in doomed:
            full_path.unlink(missing_ok=True)

    await asyncio.to_thread(unlink_all)
    return {"message": "Screenshot deleted"}


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
