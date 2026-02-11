"""Upload routes with SSE progress tracking."""

import asyncio
import json
import tempfile
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

    # Validate file sizes
    for f in files:
        if f.size and f.size > settings.max_upload_size_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File {f.filename} exceeds max size of {settings.max_upload_size_mb}MB",
            )

    # Save files to temp directory
    temp_dir = Path(tempfile.mkdtemp(prefix="gamevault_upload_"))
    temp_files = []

    for f in files:
        temp_path = temp_dir / (f.filename or "unnamed.jpg")
        content = await f.read()
        temp_path.write_bytes(content)
        temp_files.append((f.filename or "unnamed.jpg", temp_path))

    # Create task for progress tracking
    task_id = create_task_id()

    # Process in background
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

    for f in files:
        if f.size and f.size > settings.max_upload_size_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File {f.filename} exceeds max size of {settings.max_upload_size_mb}MB",
            )

    temp_dir = Path(tempfile.mkdtemp(prefix="gamevault_sync_"))
    temp_files = []

    for f in files:
        temp_path = temp_dir / (f.filename or "unnamed.jpg")
        content = await f.read()
        temp_path.write_bytes(content)
        temp_files.append((f.filename or "unnamed.jpg", temp_path))

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
