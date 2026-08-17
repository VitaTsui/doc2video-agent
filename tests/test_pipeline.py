"""End-to-end pipeline through the offline path, up to (not including) render.

Rendering needs ffmpeg or a Remotion install, so it is exercised separately; the
stages below are the ones that must hold on every machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doc2video.agent import Doc2VideoAgent
from doc2video.agent.executor import Executor
from doc2video.agent.planner import Stage
from doc2video.core.config import Settings
from doc2video.schemas import ActionType, VideoProject
from doc2video.skills.base import SkillContext
from doc2video.storage import ProjectStore

from .conftest import DEMO_PAGE_COUNT


@pytest.fixture
def built_project(settings: Settings, store: ProjectStore, demo_pptx: Path) -> VideoProject:
    agent = Doc2VideoAgent(settings, store)
    project = agent.create_project(demo_pptx)
    plan = agent.planner.initial_plan("生成一个3分钟的讲解视频，面向企业客户，第5页重点讲", project)
    plan.stages = [s for s in plan.stages if s is not Stage.RENDER]
    ctx = SkillContext.build(project, store=store, settings=settings)
    return Executor(ctx).run(plan, message="test")


def test_pipeline_produces_a_scene_per_page(built_project: VideoProject):
    assert len(built_project.document.pages) == DEMO_PAGE_COUNT
    assert len(built_project.scenes) == DEMO_PAGE_COUNT
    assert built_project.total_duration() > 0


def test_pages_have_geometry_and_rendered_images(built_project: VideoProject, store: ProjectStore):
    for page in built_project.document.pages:
        assert page.width > 0 and page.height > 0
        assert page.image_path
        assert store.resolve(built_project.project_id, page.image_path).exists()


def test_every_scene_is_voiced_with_timestamps(
    built_project: VideoProject, store: ProjectStore, settings: Settings
):
    for scene in built_project.scenes:
        assert scene.audio.path
        assert store.resolve(built_project.project_id, scene.audio.path).exists()
        assert scene.duration == pytest.approx(scene.audio.duration, abs=0.01)
        assert scene.segments
        # Speech sits inside the clip's silence, not across it: the page has
        # arrived before the first word and the last one lands before it goes.
        assert scene.segments[0].start == pytest.approx(settings.scene_lead_seconds, abs=0.05)
        assert scene.segments[-1].end == pytest.approx(
            scene.duration - settings.scene_tail_seconds, abs=0.05
        )


def test_actions_target_real_elements_and_fit_their_scene(built_project: VideoProject):
    for scene in built_project.scenes:
        page = built_project.document.page(scene.source_page)
        for action in scene.actions:
            if action.target:
                assert page.element(action.target) is not None
            assert action.at + action.duration <= scene.duration + 0.01


def test_timeline_is_contiguous_and_matches_scenes(built_project: VideoProject):
    timeline = built_project.timeline
    assert timeline.duration == pytest.approx(built_project.total_duration(), abs=0.01)
    assert len(timeline.video) == len(built_project.scenes)

    for previous, current in zip(timeline.video, timeline.video[1:], strict=False):
        assert previous.end == pytest.approx(current.start, abs=0.001)

    for cue in timeline.subtitles:
        assert 0 <= cue.start < cue.end <= timeline.duration + 0.01


def test_zoom_cues_carry_normalized_areas(built_project: VideoProject):
    zooms = [
        cue
        for cue in built_project.timeline.actions
        if cue.type in (ActionType.ZOOM, ActionType.HIGHLIGHT)
    ]
    assert zooms, "启发式导演至少应产生一个视觉动作"
    for cue in zooms:
        assert cue.area is not None
        assert 0 <= cue.area.x <= 1 and 0 <= cue.area.y <= 1
        assert cue.area.x + cue.area.w <= 1.0001


def test_project_round_trips_through_storage(built_project: VideoProject, store: ProjectStore):
    reloaded = store.load(built_project.project_id)
    assert reloaded.model_dump(mode="json") == built_project.model_dump(mode="json")


def test_scene_edit_only_touches_the_named_scene(
    built_project: VideoProject, settings: Settings, store: ProjectStore
):
    agent = Doc2VideoAgent(settings, store)
    before = {s.scene_id: s.audio.text_hash for s in built_project.scenes}

    plan = agent.planner.edit_plan("第3页太长了，压缩到8秒", built_project)
    # The script is the caller's to write; the plan only says which scene and
    # how long it should now be.
    plan.scene_narrations = {plan.scene_ids[0]: "这一页压缩后的讲稿。"}
    plan.stages = [s for s in plan.stages if s is not Stage.RENDER]
    ctx = SkillContext.build(built_project, store=store, settings=settings)
    updated = Executor(ctx).run(plan, message="第3页太长了，压缩到8秒")

    changed = [s.scene_id for s in updated.scenes if s.audio.text_hash != before[s.scene_id]]
    assert changed == ["scene_03"]
    assert updated.scene("scene_03").duration < built_project.scene("scene_01").duration + 20
    assert updated.history[-1].message == "第3页太长了，压缩到8秒"


def test_unsupported_source_is_rejected(settings: Settings, store: ProjectStore, tmp_path: Path):
    bad = tmp_path / "notes.txt"
    bad.write_text("hello", encoding="utf-8")
    agent = Doc2VideoAgent(settings, store)

    from doc2video.core.errors import UnsupportedSource

    with pytest.raises(UnsupportedSource):
        agent.create_project(bad)
