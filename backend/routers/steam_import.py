"""Steam import routes: validate, discover games, import, progress SSE, cancel."""

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse

from backend.auth import require_auth
from backend.models.steam_import import (
    SteamValidateRequest,
    SteamValidateResponse,
    SteamImportRequest,
    SteamGameInfo,
    SteamImportSessionResponse,
)
from backend.services.steam_scraper import SteamScraper
from backend.services.steam_import_service import (
    create_import_session,
    get_import_session,
    get_progress_queue,
    cleanup_session,
    request_cancel,
    run_import,
)

router = APIRouter(prefix="/api/steam", tags=["steam"], dependencies=[Depends(require_auth)])


# ── Validate credentials ─────────────────────────────────────────────────────

@router.post("/validate", response_model=SteamValidateResponse)
async def validate_steam(req: SteamValidateRequest):
    """Validate that a Steam profile exists and cookies are working."""
    try:
        async with SteamScraper(
            user_id=req.user_id,
            steam_login_secure=req.steam_login_secure,
            session_id=req.session_id,
        ) as scraper:
            profile = await scraper.validate_profile()
            return SteamValidateResponse(
                valid=True,
                profile_name=profile.profile_name,
                avatar_url=profile.avatar_url,
                is_numeric_id=profile.is_numeric_id,
            )
    except ValueError as e:
        return SteamValidateResponse(
            valid=False,
            error=str(e),
        )
    except Exception as e:
        return SteamValidateResponse(
            valid=False,
            error=f"Connection error: {e}",
        )


# ── Discover importable games ────────────────────────────────────────────────

@router.post("/games", response_model=list[SteamGameInfo])
async def list_steam_games(req: SteamValidateRequest):
    """List all games with screenshots on the user's Steam profile."""
    try:
        async with SteamScraper(
            user_id=req.user_id,
            steam_login_secure=req.steam_login_secure,
            session_id=req.session_id,
        ) as scraper:
            games = await scraper.discover_games()
            return [
                SteamGameInfo(
                    app_id=g.app_id,
                    name=g.name,
                    screenshot_count=g.screenshot_count,
                )
                for g in games
            ]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Start import ──────────────────────────────────────────────────────────────

@router.post("/import")
async def start_import(req: SteamImportRequest, bg: BackgroundTasks):
    """Start a Steam import session. Returns session_id for progress tracking."""
    session_id = await create_import_session(req.user_id)

    # Launch import as a background task
    bg.add_task(
        run_import,
        session_id=session_id,
        user_id=req.user_id,
        steam_login_secure=req.steam_login_secure,
        session_id_cookie=req.session_id,
        game_ids=req.game_ids or None,
        is_numeric_id=req.is_numeric_id,
    )

    return {"session_id": session_id}


# ── Progress SSE stream ──────────────────────────────────────────────────────

@router.get("/import/{session_id}/progress")
async def import_progress(session_id: int):
    """Server-Sent Events stream for import progress."""
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
                    # Send keepalive
                    yield ": keepalive\n\n"
                    continue

                event_type = event.get("event", "message")
                data = json.dumps(event.get("data", {}))
                yield f"event: {event_type}\ndata: {data}\n\n"

                # Terminal event — stop streaming
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


# ── Get session status ────────────────────────────────────────────────────────

@router.get("/import/{session_id}", response_model=SteamImportSessionResponse)
async def get_session(session_id: int):
    """Get the current status of an import session."""
    session = await get_import_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Import session not found")
    return SteamImportSessionResponse(**session)


# ── Cancel import ─────────────────────────────────────────────────────────────

@router.post("/import/{session_id}/cancel")
async def cancel_import(session_id: int):
    """Cancel a running import session."""
    session = await get_import_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Import session not found")

    if session["status"] != "running":
        raise HTTPException(status_code=400, detail="Import is not running")

    request_cancel(session_id)
    return {"message": "Cancel requested"}
