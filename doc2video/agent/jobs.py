"""Background job tracking.

Generating a video is a long task, so the API hands back a job id and the client
polls. Jobs keep their request so a failed one can be retried without the client
re-uploading anything (方案 §16 任务状态、失败重试).

In-process by design for the MVP; swapping in Celery or Temporal means replacing
this module only — the API talks to ``JobManager``, not to threads.

Concurrency is bounded here rather than left to the OS. A render saturates the
CPU for minutes (Chromium, then ffmpeg), so N simultaneous requests do not
finish N times faster — they finish together, much later, on a machine that
stopped responding in the meantime. Beyond the queue depth a submission is
refused outright, because a request that will not start for an hour is more
useful as an error than as a promise.
"""

from __future__ import annotations

import queue
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..core.config import Settings, get_settings
from ..core.errors import TooBusy
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
    # Set when the agent should decide for itself what to do with `message`,
    # rather than being handed a script. A chat turn may render several times,
    # which is why it is a job like any other instead of a request that waits.
    chat: bool = False


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
    # Countable work inside the current stage. Rendering and voicing both loop
    # over scenes; everything else reports 0/0 and the client shows a spinner.
    done: int = 0
    total: int = 0
    # What the agent said, for a chat turn.
    reply: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    # Live listeners. Jobs run on threads and the SSE endpoint is async, so the
    # handoff is a thread-safe queue rather than shared mutable state.
    watchers: list[queue.SimpleQueue] = field(default_factory=list, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "status": self.status,
            "stage": self.stage,
            "detail": self.detail,
            "done": self.done,
            "total": self.total,
            "reply": self.reply,
            "attempts": self.attempts,
            "project_id": self.result.project_id if self.result else self.request.project_id,
            "error": self.error,
            "result": self.result.__dict__ if self.result else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobManager:
    def __init__(self, agent: Doc2VideoAgent, settings: Settings | None = None) -> None:
        self._agent = agent
        self._settings = settings or get_settings()
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()
        self._slots = threading.Semaphore(max(1, self._settings.max_concurrent_jobs))
        self._waiting = 0

    def submit(self, request: JobRequest) -> Job:
        with self._lock:
            if self._waiting >= self._settings.max_queued_jobs:
                raise TooBusy(
                    f"排队任务已达上限（{self._settings.max_queued_jobs}），请稍后再试",
                    detail={"queued": self._waiting},
                )
            self._waiting += 1
            job = Job(id=f"job_{uuid.uuid4().hex[:12]}", request=request)
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
        with self._lock:
            self._waiting += 1
        threading.Thread(target=self._execute, args=(job,), daemon=True).start()
        return job

    # -- live progress ---------------------------------------------------
    def watch(self, job_id: str) -> queue.SimpleQueue | None:
        """Subscribe to one job's progress. ``None`` if there is no such job.

        A job that has already finished still gets a queue — seeded with its
        final state and closed — so a client that subscribes late is told the
        outcome instead of waiting for an event that will never come.
        """
        job = self.get(job_id)
        if job is None:
            return None
        channel: queue.SimpleQueue = queue.SimpleQueue()
        with self._lock:
            if job.status in ("succeeded", "failed"):
                channel.put(job.as_dict())
                channel.put(None)
            else:
                channel.put(job.as_dict())
                job.watchers.append(channel)
        return channel

    def unwatch(self, job_id: str, channel: queue.SimpleQueue) -> None:
        job = self.get(job_id)
        if job is None:
            return
        with self._lock:
            if channel in job.watchers:
                job.watchers.remove(channel)

    def _publish(self, job: Job, *, final: bool = False) -> None:
        """Hand the job's current state to every listener.

        Never blocks and never raises: a stalled or vanished client must not be
        able to hold up the render it is watching.
        """
        with self._lock:
            watchers = list(job.watchers)
            if final:
                job.watchers.clear()
        payload = job.as_dict()
        for channel in watchers:
            channel.put(payload)
            if final:
                channel.put(None)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [job.as_dict() for job in reversed(jobs[-limit:])]

    # -- worker ----------------------------------------------------------
    def _execute(self, job: Job) -> None:
        # Wait for a slot before touching the pipeline. The job is already
        # visible as "queued", so a client polling job_status sees it waiting
        # rather than wondering whether the submission was lost.
        self._slots.acquire()
        with self._lock:
            self._waiting -= 1
        job.status = "running"
        job.attempts += 1
        job.updated_at = _now()

        def progress(stage: str, detail: str, done: int = 0, total: int = 0) -> None:
            job.stage = stage
            job.detail = detail
            job.done, job.total = done, total
            job.updated_at = _now()
            self._publish(job)

        try:
            if job.request.chat:
                outcome = self._agent.chat(
                    project_id=job.request.project_id or "",
                    message=job.request.message,
                    progress=progress,
                )
                # The reply is the point of a chat turn; the project state that
                # came out of it is read the same way as any other run's.
                result = self._agent.describe(job.request.project_id or "")
                result.summary = outcome.reply
                job.reply = outcome.reply
            else:
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
            job.done = job.total = 0
        except Exception as exc:
            log.exception("任务 %s 失败", job.id)
            job.status = "failed"
            job.error = _describe(exc)
            job.detail = job.error.get("message", "")
        finally:
            job.updated_at = _now()
            self._publish(job, final=True)
            self._slots.release()
