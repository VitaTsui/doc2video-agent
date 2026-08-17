"""Run telemetry: attribution, cost, and the aggregates M4 reports from it."""

from __future__ import annotations

from pathlib import Path

from doc2video.core import telemetry
from doc2video.core.config import Settings
from doc2video.core.pricing import cost_usd
from doc2video.schemas.telemetry import LLMCall, QualityReport, RunRecord
from doc2video.storage.run_log import RunLog, summarize


def _usage(**overrides) -> telemetry.LLMUsage:
    base = {
        "provider": "anthropic_api",
        "model": "claude-opus-5",
        "input_tokens": 1000,
        "output_tokens": 500,
    }
    base.update(overrides)
    return telemetry.LLMUsage(**base)


# -- attribution ----------------------------------------------------------


def test_calls_are_attributed_to_the_stage_and_skill_that_made_them():
    with telemetry.run("proj_1") as recorder:
        with recorder.stage_scope("narrate"), telemetry.skill_scope("presentation-narration"):
            telemetry.record_llm(_usage())
        record = recorder.finish(status="succeeded")

    assert record.llm_calls[0].stage == "narrate"
    assert record.llm_calls[0].skill == "presentation-narration"


def test_scopes_restore_the_previous_attribution():
    with telemetry.run("proj_1") as recorder:
        with recorder.stage_scope("render"):
            with recorder.stage_scope("review"):
                pass
            telemetry.record_llm(_usage())
        record = recorder.finish(status="succeeded")

    assert record.llm_calls[0].stage == "render"


def test_recording_outside_a_run_is_a_no_op():
    """The CLI and tests call skills directly; that must not blow up."""
    telemetry.record_llm(_usage())
    telemetry.record_degradation("讲稿生成", "LLM 不可用")

    assert telemetry.current() is None


def test_a_failing_stage_is_recorded_as_failed_and_still_reraises():
    with telemetry.run("proj_1") as recorder:
        try:
            with recorder.stage_scope("render"):
                raise RuntimeError("ffmpeg 崩了")
        except RuntimeError:
            pass
        record = recorder.finish(status="failed")

    assert record.stages[0].status == "failed"
    assert "ffmpeg" in record.stages[0].detail


# -- cost -----------------------------------------------------------------


def test_cost_comes_from_the_price_table_when_the_provider_gives_none():
    with telemetry.run("proj_1") as recorder:
        telemetry.record_llm(_usage(input_tokens=1_000_000, output_tokens=0))
        record = recorder.finish(status="succeeded")

    assert record.llm_calls[0].cost_usd == 5.0


def test_a_provider_reported_cost_wins():
    """The CLI prices its own call, including any model it swapped in."""
    with telemetry.run("proj_1") as recorder:
        telemetry.record_llm(_usage(provider="claude_code", cost_usd=0.42))
        record = recorder.finish(status="succeeded")

    assert record.llm_calls[0].cost_usd == 0.42


def test_an_unpriced_model_reports_unknown_rather_than_zero():
    with telemetry.run("proj_1") as recorder:
        telemetry.record_llm(_usage(model="some-local-model"))
        record = recorder.finish(status="succeeded")

    assert record.llm_calls[0].cost_usd is None
    assert record.cost_usd() is None  # not 0.0 — the run's cost is unknown


def test_cache_reads_are_cheaper_than_fresh_input():
    fresh = cost_usd("claude-opus-5", input_tokens=1_000_000)
    cached = cost_usd("claude-opus-5", cache_read_tokens=1_000_000)
    written = cost_usd("claude-opus-5", cache_write_tokens=1_000_000)

    assert cached < fresh < written


def test_cost_splits_by_stage():
    with telemetry.run("proj_1") as recorder:
        with recorder.stage_scope("narrate"):
            telemetry.record_llm(_usage(input_tokens=1_000_000, output_tokens=0))
        with recorder.stage_scope("review"):
            telemetry.record_llm(_usage(input_tokens=1_000_000, output_tokens=0))
        record = recorder.finish(status="succeeded")

    assert record.cost_by_stage() == {"narrate": 5.0, "review": 5.0}


# -- degradations ---------------------------------------------------------


def test_degradations_are_recorded_so_a_worse_video_is_visible():
    """A degraded run still succeeds — counting is the only way it shows up."""
    with telemetry.run("proj_1") as recorder:
        telemetry.record_degradation("讲稿生成", "LLM 不可用")
        record = recorder.finish(status="succeeded")

    assert record.status == "succeeded"
    assert record.degradations[0].what == "讲稿生成"


# -- the ledger -----------------------------------------------------------


def _record(tmp_path: Path, **overrides) -> RunRecord:
    base = {
        "run_id": "run_1",
        "project_id": "proj_1",
        "status": "succeeded",
        "duration_s": 10.0,
    }
    base.update(overrides)
    return RunRecord(**base)


def test_runs_round_trip_through_the_ledger(tmp_path: Path):
    log = RunLog(Settings(storage_dir=tmp_path))
    log.append(_record(tmp_path, run_id="run_a"))
    log.append(_record(tmp_path, run_id="run_b"))

    assert [r.run_id for r in log.recent()] == ["run_a", "run_b"]


def test_a_truncated_line_costs_one_run_not_the_file(tmp_path: Path):
    """A crash mid-write must not make every earlier run unreadable."""
    settings = Settings(storage_dir=tmp_path)
    log = RunLog(settings)
    log.append(_record(tmp_path, run_id="run_a"))
    with log.path.open("a", encoding="utf-8") as handle:
        handle.write('{"run_id": "run_b", "proj\n')
    log.append(_record(tmp_path, run_id="run_c"))

    assert [r.run_id for r in log.recent()] == ["run_a", "run_c"]


def test_summary_of_nothing_is_not_a_crash():
    assert summarize([]) == {"runs": 0}


def test_failed_runs_count_but_do_not_pollute_the_duration_distribution(tmp_path: Path):
    records = [
        _record(tmp_path, run_id="ok", duration_s=10.0),
        _record(tmp_path, run_id="bad", status="failed", duration_s=0.2),
    ]

    summary = summarize(records)

    assert summary["failed"] == 1
    assert summary["duration_s"]["count"] == 1


def test_summary_separates_the_rollout_arms(tmp_path: Path):
    """The point of recording which arm a run took: comparing the two."""
    records = [
        _record(tmp_path, run_id="a", flags={"renderer_remotion": True}, duration_s=30.0,
                quality=QualityReport(score=90.0)),
        _record(tmp_path, run_id="b", flags={"renderer_remotion": False}, duration_s=10.0,
                quality=QualityReport(score=70.0)),
    ]

    summary = summarize(records)

    assert summary["flags"]["renderer_remotion"]["on"]["quality_median"] == 90.0
    assert summary["flags"]["renderer_remotion"]["off"]["quality_median"] == 70.0


def test_the_provider_that_answered_is_recorded_separately_from_the_arm(tmp_path: Path):
    """With no API key both arms end up on the CLI — the flag alone would lie."""
    records = [_record(tmp_path, flags={"llm_prefer_claude_code": False},
                       llm_calls=[LLMCall(provider="claude_code", model="claude-opus-5")])]

    assert summarize(records)["providers"] == {"claude_code": 1}
