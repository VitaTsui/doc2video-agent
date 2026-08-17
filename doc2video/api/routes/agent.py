"""The Agent entry point: one route handles creation and every later edit."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ...agent import JobRequest
from ...core.config import get_settings
from ...core.errors import Doc2VideoError, InvalidRequest
from ...core.logging import get_logger
from ..deps import get_agent, get_jobs

router = APIRouter(prefix="/agent", tags=["agent"])
log = get_logger(__name__)

TRUTHY = {"1", "true", "yes", "on"}


@router.post("/run")
async def run(request: Request) -> dict:
    """Create a video, or modify an existing one.

    Accepts JSON (``{"project_id": ..., "message": ...}``) for follow-up edits,
    or multipart form data with ``files`` for the first request. Runs as a
    background job by default; pass ``wait=true`` to block until it finishes.
    """
    content_type = request.headers.get("content-type", "")
    uploaded: list[Path] = []

    if content_type.startswith("application/json"):
        body = await request.json()
        message = (body.get("message") or "").strip()
        project_id = body.get("project_id")
        wait = bool(body.get("wait"))
    else:
        form = await request.form()
        message = str(form.get("message") or "").strip()
        project_id = form.get("project_id") or None
        wait = str(form.get("wait") or "").lower() in TRUTHY
        uploaded = _save_uploads(form.getlist("files"))

    if not message:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_request", "message": "message 不能为空"},
        )
    if not project_id and not uploaded:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_request", "message": "首次生成需要上传 PDF / PPT / PPTX 文件"},
        )

    agent = get_agent()
    job_request = JobRequest(message=message, project_id=project_id, files=uploaded)

    if wait:
        try:
            result = agent.run(
                message=job_request.message,
                project_id=job_request.project_id,
                files=job_request.files,
            )
        except Doc2VideoError as exc:
            raise HTTPException(status_code=exc.http_status, detail=exc.as_dict()) from exc
        return {"status": "succeeded", "result": result.__dict__}

    job = get_jobs().submit(job_request)
    return job.as_dict()


def _save_uploads(files) -> list[Path]:
    settings = get_settings()
    saved: list[Path] = []
    for upload in files:
        filename = getattr(upload, "filename", None)
        if not filename:
            continue
        target = settings.uploads_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as handle:
            shutil.copyfileobj(upload.file, handle)
        saved.append(target)
    if not saved and files:
        raise InvalidRequest("上传的文件为空")
    return saved
