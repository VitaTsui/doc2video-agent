"""Collects what a run did, while it does it.

The pipeline already reports *progress* (which stage is running) — M4 adds the
things you only learn afterwards: how long each stage took, which steps
silently degraded, and which rollout arm the run took.

The recorder lives in a ContextVar so a stage deep in the pipeline can report a
degradation without every function in between carrying a recorder argument.
Jobs run one per thread, and each thread gets its own copy, so concurrent runs
cannot cross-attribute.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from ..schemas.telemetry import Degradation, RunRecord, StageRun
from .logging import get_logger

log = get_logger(__name__)

_current: ContextVar[RunTelemetry | None] = ContextVar("doc2video_run_telemetry", default=None)


class RunTelemetry:
    """Mutable collector for one run. Finished into an immutable RunRecord."""

    def __init__(self, project_id: str, *, flags: dict[str, bool] | None = None) -> None:
        self.record = RunRecord(
            run_id=f"run_{uuid.uuid4().hex[:12]}",
            project_id=project_id,
            flags=dict(flags or {}),
        )
        self.stage = ""
        self._started = time.monotonic()

    # -- scopes ----------------------------------------------------------
    @contextmanager
    def stage_scope(self, stage: str) -> Iterator[None]:
        """Time one pipeline stage and attribute the calls made inside it."""
        previous, self.stage = self.stage, stage
        started = time.monotonic()
        status, detail = "ok", ""
        try:
            yield
        except Exception as exc:
            status, detail = "failed", str(exc)[:200]
            raise
        finally:
            self.record.stages.append(
                StageRun(
                    stage=stage,
                    duration_s=time.monotonic() - started,
                    status=status,
                    detail=detail,
                )
            )
            self.stage = previous

    # -- events ----------------------------------------------------------
    def record_degradation(self, what: str, reason: str) -> None:
        self.record.degradations.append(Degradation(what=what, reason=reason[:200]))

    def finish(self, *, status: str, message: str = "", error: str = "") -> RunRecord:
        self.record.duration_s = time.monotonic() - self._started
        self.record.status = status
        self.record.message = message
        self.record.error = error[:400]
        return self.record


# -- module-level access -------------------------------------------------
@contextmanager
def run(project_id: str, *, flags: dict[str, bool] | None = None) -> Iterator[RunTelemetry]:
    """Make a recorder the active one for this thread's call stack."""
    telemetry = RunTelemetry(project_id, flags=flags)
    token = _current.set(telemetry)
    try:
        yield telemetry
    finally:
        _current.reset(token)


def current() -> RunTelemetry | None:
    """The active recorder, or None outside a run (tests, CLI inspection)."""
    return _current.get()


def record_degradation(what: str, reason: str) -> None:
    telemetry = _current.get()
    if telemetry is not None:
        telemetry.record_degradation(what, reason)


