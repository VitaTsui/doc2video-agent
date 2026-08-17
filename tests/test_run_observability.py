"""The M4 wiring, exercised through a real agent run rather than in isolation.

Every piece is unit-tested elsewhere; what this file pins down is that the
pieces are actually *connected* — that running the agent produces a record with
real stage timings, that the record reaches both the project and the ledger, and
that a failed run is recorded rather than lost.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doc2video.agent import Doc2VideoAgent
from doc2video.agent.planner import Stage
from doc2video.core.config import Settings
from doc2video.storage import ProjectStore
from doc2video.storage.run_log import RunLog


@pytest.fixture
def agent(settings: Settings, store: ProjectStore, monkeypatch: pytest.MonkeyPatch):
    """An agent whose plans stop before render (no ffmpeg on every machine)."""
    built = Doc2VideoAgent(settings, store)
    original = built.planner.initial_plan

    def without_render(message: str, project):
        plan = original(message, project)
        plan.stages = [s for s in plan.stages if s is not Stage.RENDER]
        return plan

    monkeypatch.setattr(built.planner, "initial_plan", without_render)
    # The agent builds a fresh Planner per run; make it reuse the patched one.
    monkeypatch.setattr("doc2video.agent.service.Planner", lambda _llm: built.planner)
    return built


def test_a_run_records_its_stages_on_the_project(agent, demo_pptx: Path):
    result = agent.run(message="生成一个3分钟的讲解视频", files=[demo_pptx])
    project = agent.get_project(result.project_id)

    assert project.telemetry is not None
    assert project.telemetry.status == "succeeded"
    stages = [s.stage for s in project.telemetry.stages]
    assert "plan" in stages and "narrate" in stages
    assert all(s.duration_s >= 0 for s in project.telemetry.stages)


def test_the_run_also_lands_in_the_ledger(agent, settings: Settings, demo_pptx: Path):
    agent.run(message="生成一个3分钟的讲解视频", files=[demo_pptx])

    records = RunLog(settings).recent()

    assert len(records) == 1
    assert records[0].status == "succeeded"


def test_the_offline_path_reports_its_degradations(agent, demo_pptx: Path):
    """Without an LLM every skill falls back — a run that produced a much worse
    video must not look identical to a good one."""
    result = agent.run(message="生成一个3分钟的讲解视频", files=[demo_pptx])
    project = agent.get_project(result.project_id)

    assert project.telemetry.degradations
    assert all(d.reason for d in project.telemetry.degradations)


def test_quality_is_scored_and_surfaced_on_the_result(agent, demo_pptx: Path):
    result = agent.run(message="生成一个3分钟的讲解视频", files=[demo_pptx])
    project = agent.get_project(result.project_id)

    assert project.quality is not None
    assert result.quality == project.quality.score
    assert project.telemetry.quality is not None


def test_a_failed_run_is_recorded_rather_than_lost(
    agent, settings: Settings, demo_pptx: Path, monkeypatch: pytest.MonkeyPatch
):
    """Failures are what monitoring exists for — losing them defeats the point."""

    def explode(self, plan):  # noqa: ARG001
        raise RuntimeError("解析炸了")

    monkeypatch.setattr("doc2video.agent.executor.Executor._stage_parse", explode)

    with pytest.raises(RuntimeError):
        agent.run(message="生成一个3分钟的讲解视频", files=[demo_pptx])

    records = RunLog(settings).recent()
    assert records[-1].status == "failed"
    assert "解析炸了" in records[-1].error
    assert records[-1].stages[-1].status == "failed"


def test_the_run_records_which_rollout_arm_it_took(agent, demo_pptx: Path):
    result = agent.run(message="生成一个3分钟的讲解视频", files=[demo_pptx])
    project = agent.get_project(result.project_id)

    assert set(project.telemetry.flags) == {"llm_prefer_claude_code", "renderer_remotion"}
