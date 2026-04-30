"""Special K filesystem import routes: scan, import, progress SSE, cancel."""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse

from backend.auth import require_auth
from backend.config import settings
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/specialk", tags=["specialk"], dependencies=[Depends(require_auth)])


# ── Path allowlist (GV-010) ──────────────────────────────────────────────────
#
# Three modes:
#   1. ``specialk_allowed_roots`` set (comma-separated absolute dirs):
#      every scan/import path must resolve under one of them. Strictest.
#   2. ``specialk_allowed_roots`` empty + auth enabled: allow any path
#      (backward compatible — relies on the JWT being a sufficient
#      authorization signal for the single-user app).
#   3. ``specialk_allowed_roots`` empty + auth disabled: REFUSE. Without
#      an allowlist or auth, the endpoint becomes a public arbitrary
#      file-disclosure primitive. Operator must explicitly opt in.


def _parse_allowed_roots() -> list[Path]:
    raw = (settings.specialk_allowed_roots or "").strip()
    if not raw:
        return []
    roots: list[Path] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            roots.append(Path(chunk).resolve())
        except (OSError, RuntimeError):
            logger.warning("Could not resolve specialk_allowed_roots entry: %r", chunk)
    return roots


def _enforce_allowed_root(raw_path: str) -> Path:
    """Validate a user-supplied scan path against config.

    Returns the resolved Path on success. Raises HTTPException otherwise.
    """
    allowed = _parse_allowed_roots()

    if not allowed:
        if settings.disable_auth:
            # Mode 3: refuse entirely.
            raise HTTPException(
                status_code=403,
                detail=(
                    "Special K import is disabled when GAMEVAULT_DISABLE_AUTH=true "
                    "and no GAMEVAULT_SPECIALK_ALLOWED_ROOTS is set. Configure an "
                    "allowlist before using this endpoint without authentication."
                ),
            )
        # Mode 2: backward compatible — accept any path. The require_auth
        # dependency on the router has already gated us behind a JWT.
        try:
            return Path(raw_path).resolve()
        except (OSError, RuntimeError):
            raise HTTPException(status_code=400, detail="Invalid path")

    # Mode 1: must be under one of the configured roots.
    try:
        candidate = Path(raw_path).resolve()
    except (OSError, RuntimeError):
        raise HTTPException(status_code=400, detail="Invalid path")

    for root in allowed:
        try:
            candidate.relative_to(root)
            return candidate
        except ValueError:
            continue

    raise HTTPException(
        status_code=403,
        detail=(
            "Path is not under any configured GAMEVAULT_SPECIALK_ALLOWED_ROOTS "
            "entry. Ask the server operator to add it if you need to scan there."
        ),
    )


@router.post("/scan", response_model=SpecialKScanResponse)
async def scan(req: SpecialKScanRequest):
    """Scan a path for Special K-style per-game subfolders with screenshots."""
    raw_path = req.path.strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="Path cannot be empty")

    # Enforce the configured allowlist before touching the disk
    root = _enforce_allowed_root(raw_path)

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

    # Enforce the configured allowlist before kicking off any work
    root = _enforce_allowed_root(raw_path)
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
