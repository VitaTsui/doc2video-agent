"""How long one run took, where the time went, and how good the result was.

These are the M4 observability models. They are deliberately part of the schema
layer rather than a logging concern: a run record is persisted, served over the
API, and compared across rollout arms, so it needs the same versioned shape as
everything else the project stores.

Tokens are recorded; money is not. A price table has to be maintained against
every vendor's changes and is wrong the moment one of them moves, whereas token
counts are reported by the API itself and stay true. Anyone who wants a number
in currency can multiply — with today's prices rather than the ones that were
current when this shipped.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(UTC)


class StageRun(BaseModel):
    stage: str
    duration_s: float = 0.0
    status: str = "ok"  # ok | failed
    detail: str = ""


class Degradation(BaseModel):
    """A step that fell back to its heuristic path, and why.

    The pipeline is built to degrade rather than fail, which means a run can
    succeed while quietly producing a much worse video. Counting these is the
    only way that shows up in monitoring.
    """

    what: str
    reason: str


class LLMCall(BaseModel):
    """One model call, as the vendor reported it.

    Recorded even when the call failed or was refused: it was billed, and a run
    that spent tokens without producing anything is exactly what monitoring
    should be able to see.
    """

    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    duration_s: float = 0.0


class QualityDimension(BaseModel):
    name: str
    score: float = Field(description="0–100")
    weight: float
    detail: str = ""


class QualityReport(BaseModel):
    """A single comparable number, plus the measurements behind it.

    Deliberately derived from what the review skill already measures rather
    than from a fresh model call: a score you can only get by spending money is
    a score nobody computes on every run.
    """

    score: float = 0.0
    dimensions: list[QualityDimension] = Field(default_factory=list)
    errors: int = 0
    warnings: int = 0


class RunRecord(BaseModel):
    """One `agent.run` — the unit M4 monitors, prices and scores."""

    run_id: str
    project_id: str
    started_at: datetime = Field(default_factory=_now)
    duration_s: float = 0.0
    status: str = "running"  # running | succeeded | failed
    message: str = ""
    stages: list[StageRun] = Field(default_factory=list)
    degradations: list[Degradation] = Field(default_factory=list)
    llm_calls: list[LLMCall] = Field(default_factory=list)
    # Which arm of each rollout this run took, so cost and quality compare.
    flags: dict[str, bool] = Field(default_factory=dict)
    quality: QualityReport | None = None
    error: str = ""
