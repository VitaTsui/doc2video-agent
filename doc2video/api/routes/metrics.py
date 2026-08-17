"""Cross-run metrics: the question no single project can answer.

`/projects/{id}/telemetry` says what one run did. This says whether runs are
getting slower, what a video costs and how often steps degrade — which is the
whole reason run records are kept in a ledger rather than only on the project.
"""

from __future__ import annotations

from fastapi import APIRouter

from ...core.config import get_settings
from ...storage.run_log import RunLog

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def metrics(limit: int = 500) -> dict:
    from ...storage.run_log import summarize

    records = RunLog(get_settings()).recent(limit)
    return {"summary": summarize(records)}


@router.get("/metrics/runs")
def runs(limit: int = 20) -> dict:
    """The most recent runs, newest first — for drilling into an outlier."""
    records = RunLog(get_settings()).recent(limit)
    return {
        "items": [
            {
                "run_id": r.run_id,
                "project_id": r.project_id,
                "started_at": r.started_at.isoformat(),
                "status": r.status,
                "duration_s": round(r.duration_s, 2),
                "cost_usd": r.cost_usd(),
                "quality": r.quality.score if r.quality else None,
                "degradations": len(r.degradations),
                "flags": r.flags,
            }
            for r in reversed(records)
        ]
    }
