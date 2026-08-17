"""What one run cost, how long it took, and how good the result was.

These are the M4 observability models. They are deliberately part of the schema
layer rather than a logging concern: a run record is persisted, served over the
API, and compared across rollout arms, so it needs the same versioned shape as
everything else the project stores.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(UTC)


class LLMCall(BaseModel):
    """One model call, attributed to the stage and skill that made it."""

    stage: str = ""
    skill: str = ""
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # None when the model has no published price — not the same as free.
    cost_usd: float | None = None
    duration_s: float = 0.0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


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
    llm_calls: list[LLMCall] = Field(default_factory=list)
    degradations: list[Degradation] = Field(default_factory=list)
    # Which arm of each rollout this run took, so cost and quality compare.
    flags: dict[str, bool] = Field(default_factory=dict)
    quality: QualityReport | None = None
    error: str = ""

    # -- derived ---------------------------------------------------------
    def cost_usd(self) -> float | None:
        """Total spend, or None when any call's model had no published price."""
        if not self.llm_calls:
            return 0.0
        if any(call.cost_usd is None for call in self.llm_calls):
            return None
        return sum(call.cost_usd or 0.0 for call in self.llm_calls)

    def total_tokens(self) -> int:
        return sum(call.total_tokens for call in self.llm_calls)

    def cost_by_stage(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for call in self.llm_calls:
            totals[call.stage] = totals.get(call.stage, 0.0) + (call.cost_usd or 0.0)
        return totals
