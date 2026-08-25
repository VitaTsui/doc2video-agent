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
from doc2video.core.errors import SkillFailed
from doc2video.schemas import (
    ActionType,
    BBox,
    DocumentModel,
    DocumentPage,
    ElementKind,
    SlideElement,
    VideoProject,
)
from doc2video.schemas.project import Source, SourceType
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


def test_a_length_said_in_chat_is_applied_whether_or_not_a_model_is_configured(
    built_project: VideoProject, settings: Settings, store: ProjectStore
):
    """「压到十五分钟」 is not a matter of judgement.

    The rules read it — 900 seconds, asked for by a person — and the chat path
    handed the message to the model loop instead, which rewrote the script and
    left the target where it was. The user said fifteen minutes and got
    twenty-five.
    """
    agent = Doc2VideoAgent(settings=settings, store=store)
    built_project.intent.duration = 921
    built_project.intent.duration_stated = False
    store.save(built_project)

    updated = agent._take_what_was_stated(built_project, "压到十五分钟，另外中试基地是一个词")

    assert updated.intent.duration == 900
    assert updated.intent.duration_stated is True
    assert updated.intent.pronunciation == {"中试基地": " 中试基地"}
    # And it survives the reload, because the next stage loads from disk.
    assert store.load(built_project.project_id).intent.duration == 900
# -- a deck nobody can read ----------------------------------------------
class _Sighted:
    """A model that can be shown a page render."""

    source = "stub"
    available = True

    def supports_images(self) -> bool:
        return True


class _Blind(_Sighted):
    """One that cannot — the CLI runtime before it could carry images."""

    def supports_images(self) -> bool:
        return False


def _scan(settings: Settings, store: ProjectStore, llm, *, pages: int, words: str = "") -> Executor:
    """A deck whose pages carry no text, the way a phone scan parses."""
    project = VideoProject(
        project_id="scan",
        source=Source(type=SourceType.PDF, file="scan.pdf", path="source/scan.pdf"),
    )
    project.document = DocumentModel(
        title="scan",
        pages=[
            DocumentPage(index=i, image_path=f"assets/page_{i:03d}.png")
            for i in range(1, pages + 1)
        ],
    )
    if words:
        project.document.pages[0].elements = [
            SlideElement(
                id="p01_e01",
                kind=ElementKind.PARAGRAPH,
                text=words,
                bbox=BBox(x=0, y=0, w=100, h=20),
            )
        ]
    return Executor(SkillContext(project=project, store=store, settings=settings, llm=llm))


def test_a_deck_with_no_text_at_all_is_stopped_before_anything_is_spent(
    settings: Settings, store: ProjectStore
):
    """Nine pages of pictures and a model that cannot look at them.

    Every stage downstream succeeds on this input and the result is a video
    with no content in it — scored 95.5 by a review whose checks all had
    nothing to compare against. The only place it can be caught is here.
    """
    executor = _scan(settings, store, _Blind(), pages=9)

    with pytest.raises(SkillFailed) as caught:
        executor._check_the_deck_was_read()

    assert "一个字都没解析出来" in str(caught.value)
    assert caught.value.detail["pages"] == 9


def test_the_same_deck_goes_through_when_the_model_can_see_the_pages(
    settings: Settings, store: ProjectStore, caplog
):
    """The page renders are the way out, so this is not fatal on its own."""
    executor = _scan(settings, store, _Sighted(), pages=9)

    with caplog.at_level("WARNING"):
        executor._check_the_deck_was_read()  # does not raise

    assert "只能靠页面图理解" in caplog.text


def test_a_deck_that_parsed_normally_is_left_alone(settings: Settings, store: ProjectStore):
    """The gate must not fire on a deck with sparse slides.

    One page of text among nine is enough to prove the text layer is there —
    the other eight being covers and diagrams is what decks look like.
    """
    executor = _scan(settings, store, _Blind(), pages=9, words="这一页是有字的")

    executor._check_the_deck_was_read()  # does not raise



def test_pages_are_spoken_several_at_a_time():
    """Thirty pages one after another was nine minutes of a forty-minute film.

    No page waits on any other — each is its own text, its own file, its own
    engine process — so the only reason it took that long was that nothing
    asked for them at once.
    """
    from doc2video.skills.voice import speaking_workers

    assert speaking_workers(30, 0) > 1, "默认就该并行"
    assert speaking_workers(30, 4) == 4, "配置了就听配置的"
    assert speaking_workers(1, 8) == 1, "只有一页就没什么好并的"
    assert speaking_workers(2, 8) == 2, "不会开得比页数还多"


def test_scenes_are_drawn_fewer_at_a_time_than_pages_are_spoken():
    """Drawing is a browser and an encoder; speaking is mostly waiting.

    Running as many browsers as there are cores leaves nothing for the encode
    each of them ends with.
    """
    import os

    from doc2video.agent.executor import drawing_workers
    from doc2video.skills.voice import speaking_workers

    cores = os.cpu_count() or 4
    if cores >= 4:
        assert drawing_workers(30, 0) < speaking_workers(30, 0)
    assert drawing_workers(30, 3) == 3
    assert drawing_workers(1, 8) == 1


def test_a_worker_still_writes_to_the_account():
    """The record lives on a context variable.

    Work handed to a thread that does not carry the calling context records its
    calls nowhere, and a parallel run would leave an empty ledger — the one
    thing that explains where a slow run spent its time.
    """
    import inspect

    from doc2video.agent import executor
    from doc2video.skills import voice

    for module in (voice, executor):
        source = inspect.getsource(module)
        assert "copy_context()" in source, f"{module.__name__} 没有把账本上下文带进线程"


def test_batches_are_written_a_few_at_a_time():
    """Batches do not see each other, so writing them in turn bought nothing.

    Each is given its own pages and the ones a person already wrote; nothing a
    batch produces reaches the next one. Waiting for one before starting the
    next spent fifteen minutes of a forty-minute film on nothing.
    """
    from doc2video.skills.narration import MAX_WRITERS, writing_workers

    assert writing_workers(8, 0) > 1, "默认就该并行"
    assert writing_workers(8, 0) <= MAX_WRITERS, "上限不是机器，是模型后面那道闸"
    assert writing_workers(8, 2) == 2, "配置了就听配置的"
    assert writing_workers(1, 8) == 1


def test_writing_a_batch_touches_nothing_shared():
    """Two batches saving the project at the same moment is a corrupted file.

    The call and its validation happen on the worker; the drafts, the scenes
    and the save stay on the thread that collects the results.
    """
    import inspect

    from doc2video.skills.narration import NarrationSkill

    source = inspect.getsource(NarrationSkill._write_batch)
    for forbidden in ("self.ctx.store.save", "drafts[", "_build_scenes"):
        assert forbidden not in source, f"_write_batch 不该碰 {forbidden}"
