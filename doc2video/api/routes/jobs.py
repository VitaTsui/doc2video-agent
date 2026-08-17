"""Job status and retry."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import get_jobs

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def list_jobs(limit: int = 50) -> dict:
    return {"items": get_jobs().list_jobs(limit)}


@router.get("/{job_id}")
def get_job(job_id: str) -> dict:
    job = get_jobs().get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404, detail={"code": "job_not_found", "message": "任务不存在"}
        )
    return job.as_dict()


@router.post("/{job_id}/retry")
def retry_job(job_id: str) -> dict:
    try:
        return get_jobs().retry(job_id).as_dict()
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "job_not_found", "message": "任务不存在"}
        ) from exc
