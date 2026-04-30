"""Upload routes with SSE progress tracking."""

import asyncio
import json
import re
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from backend.auth import require_auth
from backend.config import settings
from backend.services.upload_service import (
    cleanup_progress,
    create_task_id,
    get_progress_queue,
    process_upload,
)

router = APIRouter(prefix="/api/upload", tags=["upload"], dependencies=[Depends(require_auth)])

# Read uploads in 1 MB chunks so we never buffer the whole file in memory.
_UPLOAD_CHUNK_SIZE = 1024 * 1024

# Whitelist of characters allowed in the *display* filename (used for
# progress events + log lines only — the actual on-disk path is a uuid).
_DISPLAY_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._\- ()]")


def _safe_display_name(filename: str | None) -> str:
    """Sanitize the user-supplied filename for use as a *display* string.

    The on-disk temp path is always a random UUID — this function only
    cleans up the name so it can appear in progress events / logs without
    enabling log injection. Strips any path components, drops control
    characters, and caps the length.
    """
    if not filename:
        return "unnamed.jpg"
    base = Path(filename).name  # strips directories on POSIX and Windows
    base = _DISPLAY_NAME_PATTERN.sub("_", base).strip()
    return (base[:120] or "unnamed.jpg")


async def _save_upload_streaming(
    f: UploadFile,
    temp_dir: Path,
    max_bytes: int,
) -> tuple[str, Path]:
    """Stream an UploadFile to a uuid-named temp file with a hard byte cap.

    Returns ``(display_name, on_disk_path)``. The on-disk path is built from
    a random UUID — the user-supplied filename is *never* used as part of
    any path. Aborts with HTTP 413 as soon as the byte limit is exceeded,
    so a malicious oversized upload doesn't get fully buffered.
    """
    display_name = _safe_display_name(f.filename)
    temp_path = temp_dir / f"{uuid.uuid4().hex}.bin"

    written = 0
    try:
        with open(temp_path, "wb") as out:
            while True:
                chunk = await f.read(_UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    out.close()
                    try:
                        temp_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"File {display_name} exceeds max size of "
                            f"{settings.max_upload_size_mb}MB"
                        ),
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception:
        # Best-effort cleanup on unexpected failure
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    return display_name, temp_path


@router.post("")
async def upload_screenshots(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = [],
    game_id: int = Form(...),
    taken_at: str = Form(default=None),
):
    """Upload one or more screenshot files.

    Returns a task_id for SSE progress tracking.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    # Fast-path size pre-check: if Content-Length is known and over the limit,
    # reject immediately without buffering. This is best-effort — the real
    # enforcement happens in _save_upload_streaming.
    max_bytes = settings.max_upload_size_bytes
    for f in files:
        if f.size and f.size > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"File {_safe_display_name(f.filename)} exceeds max size "
                    f"of {settings.max_upload_size_mb}MB"
                ),
            )

    temp_dir = Path(tempfile.mkdtemp(prefix="gamevault_upload_"))
    temp_files: list[tuple[str, Path]] = []

    try:
        for f in files:
            display_name, temp_path = await _save_upload_streaming(
                f, temp_dir, max_bytes
            )
            temp_files.append((display_name, temp_path))
    except HTTPException:
        # Cleanup partial uploads if any one file blew the limit
        import shutil

        shutil.rmtree(str(temp_dir), ignore_errors=True)
        raise

    task_id = create_task_id()

    background_tasks.add_task(
        _run_upload,
        task_id,
        game_id,
        temp_files,
        taken_at,
        temp_dir,
    )

    return {"task_id": task_id, "file_count": len(files)}


async def _run_upload(
    task_id: str,
    game_id: int,
    temp_files: list[tuple[str, Path]],
    taken_at: str | None,
    temp_dir: Path,
):
    """Background task wrapper for upload processing."""
    try:
        await process_upload(task_id, game_id, temp_files, taken_at)
    finally:
        # Clean up temp directory
        import shutil
        try:
            shutil.rmtree(str(temp_dir), ignore_errors=True)
        except Exception:
            pass


@router.post("/sync")
async def upload_screenshot_sync(
    files: list[UploadFile] = [],
    game_id: int = Form(...),
    taken_at: str = Form(default=None),
):
    """Synchronous upload — processes files inline and returns results.

    Used by the CLI sync tool which needs to know immediately if the upload
    succeeded (unlike the SSE-based ``POST /api/upload`` used by the web UI).
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    max_bytes = settings.max_upload_size_bytes
    for f in files:
        if f.size and f.size > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"File {_safe_display_name(f.filename)} exceeds max size "
                    f"of {settings.max_upload_size_mb}MB"
                ),
            )

    temp_dir = Path(tempfile.mkdtemp(prefix="gamevault_sync_"))
    temp_files: list[tuple[str, Path]] = []

    try:
        for f in files:
            display_name, temp_path = await _save_upload_streaming(
                f, temp_dir, max_bytes
            )
            temp_files.append((display_name, temp_path))
    except HTTPException:
        import shutil

        shutil.rmtree(str(temp_dir), ignore_errors=True)
        raise

    task_id = create_task_id()

    try:
        results = await process_upload(task_id, game_id, temp_files, taken_at)
        return {"uploaded": len(results), "screenshots": results}
    finally:
        import shutil
        shutil.rmtree(str(temp_dir), ignore_errors=True)
        cleanup_progress(task_id)


@router.get("/progress/{task_id}")
async def upload_progress(task_id: str):
    """SSE endpoint for upload progress.

    Streams progress events as Server-Sent Events.
    """
    queue = get_progress_queue(task_id)

    async def event_stream():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("type") == "complete":
                        break
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"
        finally:
            cleanup_progress(task_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering for SSE
        },
    )
