"""Collects what a run did, while it does it.

The pipeline already reports *progress* (which stage is running) — M4 adds the
things you only learn afterwards: how long each stage took, what every model
call cost and which skill made it, which steps silently degraded, and which
rollout arm the run took.

Attribution travels in a ContextVar rather than through every signature. A
model call happens deep inside a skill, three layers below the executor that
knows the stage name, and the LLM tool is deliberately ignorant of both — the
alternative was threading a recorder through the whole tool interface, which
would put an observability concern into the contract skills depend on. Jobs run
one per thread, and each thread gets its own ContextVar copy, so concurrent
runs cannot cross-attribute.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from ..schemas.telemetry import Degradation, LLMCall, RunRecord, StageRun
from .logging import get_logger
from .pricing import cost_usd

log = get_logger(__name__)

_current: ContextVar[RunTelemetry | None] = ContextVar("doc2video_run_telemetry", default=None)


@dataclass
class LLMUsage:
    """What a provider reports after one call.

    ``cost_usd`` is optional because providers differ: the Claude Code CLI
    reports its own figure, while the API path leaves it to the price table.
    """

    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    duration_s: float = 0.0
    cost_usd: float | None = None


class RunTelemetry:
    """Mutable collector for one run. Finished into an immutable RunRecord."""

    def __init__(self, project_id: str, *, flags: dict[str, bool] | None = None) -> None:
        self.record = RunRecord(
            run_id=f"run_{uuid.uuid4().hex[:12]}",
            project_id=project_id,
            flags=dict(flags or {}),
        )
        self.stage = ""
        self.skill = ""
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

    @contextmanager
    def skill_scope(self, skill: str) -> Iterator[None]:
        previous, self.skill = self.skill, skill
        try:
            yield
        finally:
            self.skill = previous

    # -- events ----------------------------------------------------------
    def record_llm(self, usage: LLMUsage) -> None:
        cost = usage.cost_usd
        if cost is None:
            cost = cost_usd(
                usage.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
            )
        self.record.llm_calls.append(
            LLMCall(
                stage=self.stage,
                skill=self.skill,
                provider=usage.provider,
                model=usage.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                cost_usd=cost,
                duration_s=usage.duration_s,
            )
        )

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


def record_llm(usage: LLMUsage) -> None:
    """Called by LLM providers. A no-op outside a run — never a failure."""
    telemetry = _current.get()
    if telemetry is not None:
        telemetry.record_llm(usage)


def record_degradation(what: str, reason: str) -> None:
    telemetry = _current.get()
    if telemetry is not None:
        telemetry.record_degradation(what, reason)


@contextmanager
def skill_scope(skill: str) -> Iterator[None]:
    telemetry = _current.get()
    if telemetry is None:
        yield
        return
    with telemetry.skill_scope(skill):
        yield
