"""Duplicate scan and review routes."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from backend.auth import require_auth
from backend.services import duplicate_service

router = APIRouter(
    prefix="/api/duplicates",
    tags=["duplicates"],
    dependencies=[Depends(require_auth)],
)


@router.get("/scan")
async def get_scan_status():
    return await duplicate_service.get_latest_scan_job()


@router.post("/scan")
async def start_scan(background_tasks: BackgroundTasks):
    job = await duplicate_service.start_scan_job()
    background_tasks.add_task(duplicate_service.run_scan_job, job["id"])
    return job


@router.get("/candidates")
async def get_candidates(
    status: str = "pending",
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
):
    candidates, total = await duplicate_service.list_candidates(status, page, limit)
    return {
        "candidates": candidates,
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
    }


@router.post("/candidates/{candidate_id}/group")
async def group_candidate(candidate_id: int):
    try:
        return await duplicate_service.group_candidate(candidate_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/candidates/{candidate_id}/dismiss")
async def dismiss_candidate(candidate_id: int):
    try:
        return await duplicate_service.dismiss_candidate(candidate_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
