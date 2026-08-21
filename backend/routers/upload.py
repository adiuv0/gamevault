"""Upload routes with SSE progress tracking."""

import asyncio
import json
import re
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from backend.auth import require_auth
from backend.config import settings
from backend.services.game_service import get_game
from backend.services.upload_job_service import (
    activate_staging_area,
    cleanup_staging_area,
    create_staging_area,
    create_upload_job,
    get_upload_job,
    is_terminal_upload,
    upload_snapshot_event,
)
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
    files: list[UploadFile] = [],
    game_id: int = Form(...),
    taken_at: str = Form(default=None),
):
    """Upload one or more screenshot files.

    Returns a task_id for SSE progress tracking.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if not await get_game(game_id):
        raise HTTPException(status_code=404, detail="Game not found")

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

    task_id = create_task_id()
    temp_dir = create_staging_area(task_id)
    temp_files: list[tuple[str, Path]] = []
    registered = False

    try:
        for f in files:
            display_name, temp_path = await _save_upload_streaming(
                f, temp_dir, max_bytes
            )
            temp_files.append((display_name, temp_path))
        activate_staging_area(task_id)
        await create_upload_job(
            task_id,
            game_id,
            taken_at,
            [
                {"display_name": display_name, "storage_name": path.name}
                for display_name, path in temp_files
            ],
        )
        registered = True
    finally:
        if not registered:
            cleanup_staging_area(task_id)

    return {"task_id": task_id, "file_count": len(files)}


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
    temp_files: list[tuple[int, str, Path]] = []

    try:
        for f in files:
            display_name, temp_path = await _save_upload_streaming(
                f, temp_dir, max_bytes
            )
            temp_files.append((len(temp_files), display_name, temp_path))
    except HTTPException:
        shutil.rmtree(str(temp_dir), ignore_errors=True)
        raise

    task_id = create_task_id()

    try:
        results = await process_upload(task_id, game_id, temp_files, taken_at)
        return {"uploaded": len(results), "screenshots": results}
    finally:
        shutil.rmtree(str(temp_dir), ignore_errors=True)
        cleanup_progress(task_id)


@router.get("/jobs/{task_id}")
async def get_upload_job_status(task_id: str):
    """Return durable upload and per-file progress."""
    try:
        job = await get_upload_job(task_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Upload job not found")
    if job is None:
        raise HTTPException(status_code=404, detail="Upload job not found")
    return upload_snapshot_event(job)


@router.get("/progress/{task_id}")
async def upload_progress(task_id: str):
    """SSE endpoint for upload progress.

    Streams progress events as Server-Sent Events.
    """
    queue = get_progress_queue(task_id)
    try:
        initial_job = await get_upload_job(task_id)
    except ValueError:
        initial_job = None
    if initial_job is None:
        cleanup_progress(task_id)
        raise HTTPException(status_code=404, detail="Upload job not found")

    async def event_stream():
        try:
            job = await get_upload_job(task_id)
            if job is None:
                return
            yield f"data: {json.dumps(upload_snapshot_event(job))}\n\n"
            if is_terminal_upload(job):
                yield f"data: {json.dumps({'type': 'complete'})}\n\n"
                return

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
