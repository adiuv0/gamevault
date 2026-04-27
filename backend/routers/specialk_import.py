"""Special K filesystem import routes: scan, import, progress SSE, cancel."""

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse

from backend.auth import require_auth
from backend.models.specialk_import import (
    SpecialKImportRequest,
    SpecialKImportSessionResponse,
    SpecialKScanGame,
    SpecialKScanRequest,
    SpecialKScanResponse,
)
from backend.services.specialk_import_service import (
    cleanup_session,
    create_import_session,
    get_import_session,
    get_progress_queue,
    request_cancel,
    run_import,
    scan_path,
)

router = APIRouter(prefix="/api/specialk", tags=["specialk"], dependencies=[Depends(require_auth)])


@router.post("/scan", response_model=SpecialKScanResponse)
async def scan(req: SpecialKScanRequest):
    """Scan a path for Special K-style per-game subfolders with screenshots."""
    raw_path = req.path.strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="Path cannot be empty")

    root = Path(raw_path)
    if not root.exists():
        return SpecialKScanResponse(
            valid=False,
            path=raw_path,
            error=f"Path does not exist: {raw_path}",
        )
    if not root.is_dir():
        return SpecialKScanResponse(
            valid=False,
            path=raw_path,
            error=f"Path is not a directory: {raw_path}",
        )

    games = await asyncio.to_thread(scan_path, root)
    if not games:
        return SpecialKScanResponse(
            valid=False,
            path=raw_path,
            error="No screenshots found in any subfolder.",
        )

    return SpecialKScanResponse(
        valid=True,
        path=raw_path,
        total_games=len(games),
        total_screenshots=sum(len(g.files) for g in games),
        games=[
            SpecialKScanGame(
                folder_name=g.folder_name,
                suggested_name=g.suggested_name,
                screenshot_count=len(g.files),
                has_hdr=g.has_hdr,
                has_sdr=g.has_sdr,
            )
            for g in games
        ],
    )


@router.post("/import")
async def start_import(req: SpecialKImportRequest, bg: BackgroundTasks):
    """Start a Special K import session. Returns session_id for SSE."""
    raw_path = req.path.strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="Path cannot be empty")

    root = Path(raw_path)
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {raw_path}")

    session_id = await create_import_session(raw_path)
    bg.add_task(
        run_import,
        session_id=session_id,
        scan_path_str=raw_path,
        folder_names=req.folder_names or None,
    )
    return {"session_id": session_id}


@router.get("/import/{session_id}/progress")
async def import_progress(session_id: int):
    """Server-Sent Events stream for Special K import progress."""
    session = await get_import_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Import session not found")

    async def event_generator():
        queue = get_progress_queue(session_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue

                event_type = event.get("event", "message")
                data = json.dumps(event.get("data", {}))
                yield f"event: {event_type}\ndata: {data}\n\n"

                if event_type == "done":
                    break
        finally:
            cleanup_session(session_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/import/{session_id}", response_model=SpecialKImportSessionResponse)
async def get_session(session_id: int):
    """Get the current status of a Special K import session."""
    session = await get_import_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Import session not found")
    return SpecialKImportSessionResponse(**session)


@router.post("/import/{session_id}/cancel")
async def cancel_import(session_id: int):
    """Cancel a running Special K import."""
    session = await get_import_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Import session not found")
    if session["status"] != "running":
        raise HTTPException(status_code=400, detail="Import is not running")
    request_cancel(session_id)
    return {"message": "Cancel requested"}
