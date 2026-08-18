"""Job status, live progress, and retry."""

from __future__ import annotations

import asyncio
import json
import queue

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..deps import get_jobs

# How long to wait for an event before sending a keep-alive comment. Proxies
# and browsers both drop a silent connection, and a render stage can legitimately
# say nothing for minutes.
HEARTBEAT_SECONDS = 15

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


@router.get("/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    """Server-sent events for one job, ending when the job does.

    Polling was the only option before this, and it reads badly against this
    pipeline: progress moves in a handful of discrete steps and the render
    stage can sit on one of them for minutes, so a poller either hammers the
    endpoint or shows a frozen bar. The events are the same payload as
    ``GET /jobs/{id}``, so a client can use either without a second parser.
    """
    channel = get_jobs().watch(job_id)
    if channel is None:
        raise HTTPException(
            status_code=404, detail={"code": "job_not_found", "message": "任务不存在"}
        )

    async def stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    # The job runs on a thread; blocking for it must not block
                    # the event loop.
                    payload = await asyncio.to_thread(channel.get, True, HEARTBEAT_SECONDS)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                if payload is None:
                    yield "event: done\ndata: {}\n\n"
                    break
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        finally:
            get_jobs().unwatch(job_id, channel)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
