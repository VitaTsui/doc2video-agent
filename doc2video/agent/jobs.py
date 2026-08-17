"""Background job tracking.

Generating a video is a long task, so the API hands back a job id and the client
polls. Jobs keep their request so a failed one can be retried without the client
re-uploading anything (方案 §16 任务状态、失败重试).

In-process by design for the MVP; swapping in Celery or Temporal means replacing
this module only — the API talks to ``JobManager``, not to threads.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..core.logging import get_logger
from .service import AgentRunResult, Doc2VideoAgent

log = get_logger(__name__)

MAX_JOBS_RETAINED = 200


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _describe(exc: Exception) -> dict[str, Any]:
    """Domain errors carry a structured payload; anything else gets a generic one."""
    as_dict = getattr(exc, "as_dict", None)
    if callable(as_dict):
        return as_dict()
    return {"code": "internal_error", "message": str(exc)}


@dataclass
class JobRequest:
    message: str
    project_id: str | None = None
    files: list[Path] = field(default_factory=list)
    # The caller's script. By page index for a whole run, by scene id for a
    # targeted revision — this service writes neither.
    narrations: dict[int, str] = field(default_factory=dict)
    scene_narrations: dict[str, str] = field(default_factory=dict)


@dataclass
class Job:
    id: str
    request: JobRequest
    status: str = "queued"  # queued | running | succeeded | failed
    stage: str = ""
    detail: str = ""
    error: dict[str, Any] | None = None
    result: AgentRunResult | None = None
    attempts: int = 0
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "status": self.status,
            "stage": self.stage,
            "detail": self.detail,
            "attempts": self.attempts,
            "project_id": self.result.project_id if self.result else self.request.project_id,
            "error": self.error,
            "result": self.result.__dict__ if self.result else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobManager:
    def __init__(self, agent: Doc2VideoAgent) -> None:
        self._agent = agent
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()

    def submit(self, request: JobRequest) -> Job:
        job = Job(id=f"job_{uuid.uuid4().hex[:12]}", request=request)
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > MAX_JOBS_RETAINED:
                self._jobs.popitem(last=False)
        threading.Thread(target=self._execute, args=(job,), daemon=True).start()
        return job

    def retry(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.status == "running":
            return job
        # Retry against the project the first attempt created, so a failure late
        # in the pipeline does not re-parse and re-voice from scratch.
        if job.result is not None:
            job.request.project_id = job.result.project_id
        job.status = "queued"
        job.error = None
        threading.Thread(target=self._execute, args=(job,), daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [job.as_dict() for job in reversed(jobs[-limit:])]

    # -- worker ----------------------------------------------------------
    def _execute(self, job: Job) -> None:
        job.status = "running"
        job.attempts += 1
        job.updated_at = _now()

        def progress(stage: str, detail: str) -> None:
            job.stage = stage
            job.detail = detail
            job.updated_at = _now()

        try:
            result = self._agent.run(
                message=job.request.message,
                project_id=job.request.project_id,
                files=job.request.files,
                progress=progress,
                narrations=job.request.narrations,
                scene_narrations=job.request.scene_narrations,
            )
            job.result = result
            job.status = "succeeded"
            job.stage = "done"
            job.detail = result.summary
        except Exception as exc:
            log.exception("任务 %s 失败", job.id)
            job.status = "failed"
            job.error = _describe(exc)
            job.detail = job.error.get("message", "")
        finally:
            job.updated_at = _now()
