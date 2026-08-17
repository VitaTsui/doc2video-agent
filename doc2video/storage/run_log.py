"""Append-only ledger of run records, and the aggregates M4 reports from it.

A run record lives in two places on purpose. The project keeps its *latest*
one, because that is what "how did this video turn out" means. The ledger keeps
*all* of them, because every question M4 was built to answer — is this stage
getting slower, what does a video cost, is the new renderer arm scoring worse —
is a question about many runs, and the project files cannot answer it without
walking the whole store.

JSONL because the access pattern is append-once / read-all, and a corrupt line
should cost one run rather than the file.
"""

from __future__ import annotations

import threading
from statistics import median

from ..core.config import Settings, get_settings
from ..core.logging import get_logger
from ..schemas.telemetry import RunRecord

log = get_logger(__name__)

RUN_LOG_FILE = "runs.jsonl"
# Bounds the aggregate scan. Old runs stay on disk; they just stop being read.
MAX_RUNS_SCANNED = 2000


class RunLog:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self.path = self._settings.storage_dir / RUN_LOG_FILE
        self._lock = threading.Lock()

    def append(self, record: RunRecord) -> None:
        """Record one run. Never raises — losing telemetry must not fail a job."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = record.model_dump_json()
            with self._lock, self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            log.warning("写入运行日志失败：%s", exc)

    def recent(self, limit: int = MAX_RUNS_SCANNED) -> list[RunRecord]:
        """The newest ``limit`` runs, oldest first. Bad lines are skipped."""
        if not self.path.exists():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            log.warning("读取运行日志失败：%s", exc)
            return []

        records: list[RunRecord] = []
        for line in lines[-limit:]:
            if not line.strip():
                continue
            try:
                records.append(RunRecord.model_validate_json(line))
            except ValueError:
                # One truncated line (a crash mid-write) must not hide the rest.
                log.debug("跳过一条无法解析的运行记录")
        return records


def summarize(records: list[RunRecord]) -> dict[str, object]:
    """Aggregate runs into the numbers `doctor` and /metrics report.

    p95 alongside the median because stage duration is what a user waits on,
    and the tail is the part that hurts.
    """
    if not records:
        return {"runs": 0}

    succeeded = [r for r in records if r.status == "succeeded"]
    costs = [c for c in (r.cost_usd() for r in succeeded) if c is not None]
    qualities = [r.quality.score for r in succeeded if r.quality is not None]

    return {
        "runs": len(records),
        "succeeded": len(succeeded),
        "failed": sum(1 for r in records if r.status == "failed"),
        "duration_s": _distribution([r.duration_s for r in succeeded]),
        "cost_usd": {
            "total": round(sum(costs), 4),
            "per_run_median": round(median(costs), 4) if costs else None,
            "priced_runs": len(costs),
        },
        "tokens": sum(r.total_tokens() for r in records),
        # Quality reports its *worst* tail, not p95: for duration the bad tail
        # is the slow end, for a score it is the low end. A p95 quality figure
        # would report the best runs and read as reassurance.
        "quality": _quality_distribution(qualities),
        "stages": _stage_summary(records),
        "degradations": _degradation_counts(records),
        # Which provider actually answered, which is not the same question as
        # which rollout arm the run took: with no API key configured, both arms
        # of the provider flag end up on the CLI.
        "providers": _provider_counts(records),
        "flags": _flag_summary(succeeded),
    }


def _distribution(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"count": 0, "median": None, "p95": None}
    ordered = sorted(values)
    # Nearest-rank p95: with few samples this is the worst run, which is the
    # honest answer — interpolating would invent a value nothing produced.
    index = max(0, min(len(ordered) - 1, round(0.95 * len(ordered)) - 1))
    return {
        "count": len(ordered),
        "median": round(median(ordered), 3),
        "p95": round(ordered[index], 3),
    }


def _quality_distribution(values: list[float]) -> dict[str, float | None]:
    """Median plus the 5th percentile — the runs that came out worst."""
    if not values:
        return {"count": 0, "median": None, "p5": None}
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(0.05 * len(ordered)) - 1))
    return {
        "count": len(ordered),
        "median": round(median(ordered), 1),
        "p5": round(ordered[index], 1),
    }


def _stage_summary(records: list[RunRecord]) -> dict[str, dict[str, object]]:
    durations: dict[str, list[float]] = {}
    failures: dict[str, int] = {}
    for record in records:
        for stage in record.stages:
            durations.setdefault(stage.stage, []).append(stage.duration_s)
            if stage.status == "failed":
                failures[stage.stage] = failures.get(stage.stage, 0) + 1
    return {
        name: {**_distribution(values), "failed": failures.get(name, 0)}
        for name, values in durations.items()
    }


def _degradation_counts(records: list[RunRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        for degradation in record.degradations:
            counts[degradation.what] = counts.get(degradation.what, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))


def _provider_counts(records: list[RunRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        for call in record.llm_calls:
            counts[call.provider] = counts.get(call.provider, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))


def _flag_summary(records: list[RunRecord]) -> dict[str, dict[str, dict[str, object]]]:
    """Cost and quality per rollout arm — the point of recording flags at all."""
    arms: dict[str, dict[bool, list[RunRecord]]] = {}
    for record in records:
        for name, value in record.flags.items():
            arms.setdefault(name, {}).setdefault(value, []).append(record)

    summary: dict[str, dict[str, dict[str, object]]] = {}
    for name, by_value in arms.items():
        summary[name] = {}
        for value, runs in by_value.items():
            costs = [c for c in (r.cost_usd() for r in runs) if c is not None]
            scores = [r.quality.score for r in runs if r.quality is not None]
            summary[name]["on" if value else "off"] = {
                "runs": len(runs),
                "cost_usd_median": round(median(costs), 4) if costs else None,
                "quality_median": round(median(scores), 1) if scores else None,
                "duration_s_median": round(median([r.duration_s for r in runs]), 2),
            }
    return summary


def load_summary(settings: Settings | None = None) -> dict[str, object]:
    settings = settings or get_settings()
    return summarize(RunLog(settings).recent())


__all__ = ["RUN_LOG_FILE", "RunLog", "load_summary", "summarize"]
