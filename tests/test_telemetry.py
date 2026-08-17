"""Run telemetry: stage timings, degradations, and the cross-run aggregates."""

from __future__ import annotations

from pathlib import Path

from doc2video.core import telemetry
from doc2video.core.config import Settings
from doc2video.schemas.telemetry import QualityReport, RunRecord
from doc2video.storage.run_log import RunLog, summarize

# -- stages ---------------------------------------------------------------


def test_nested_stages_restore_the_outer_one():
    with telemetry.run("proj_1") as recorder:
        with recorder.stage_scope("render"):
            with recorder.stage_scope("review"):
                pass
            telemetry.record_degradation("字幕", "drawtext 缺失")
        record = recorder.finish(status="succeeded")

    assert [s.stage for s in record.stages] == ["review", "render"]


def test_recording_outside_a_run_is_a_no_op():
    """The CLI and tests call skills directly; that must not blow up."""
    telemetry.record_degradation("讲稿", "调用方未提供")

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
