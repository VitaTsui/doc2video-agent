"""Editing one page by describing the change.

"第 3 页太长了，压到 20 秒" is the whole point of a conversational interface,
and it used to be a no-op: the planner produced a scene id and a target
duration, the executor found no replacement *text*, and the run re-voiced and
re-rendered the same words while reporting "修改 1 个场景".
"""

from __future__ import annotations

from doc2video.agent.planner import Planner, Stage
from doc2video.core.config import Settings
from doc2video.schemas import DocumentPage, Scene, Source, SourceType, VideoProject
from doc2video.skills import NarrationSkill
from doc2video.skills.base import SkillContext
from doc2video.storage import ProjectStore
from doc2video.tools.llm import MockLLM


def _project() -> VideoProject:
    project = VideoProject(
        project_id="proj_edit",
        source=Source(type=SourceType.PPTX, file="d.pptx", path="source/d.pptx", page_count=3),
    )
    project.scenes = [
        Scene(scene_id="scene_03", source_page=3, narration="原来的讲稿。", duration=40.0)
    ]
    return project


def test_the_planner_carries_the_users_words_to_the_scene():
    project = _project()
    plan = Planner().edit_plan("第 3 页太长了，压到 20 秒", project)

    assert plan.scene_ids == ["scene_03"]
    assert "压到 20 秒" in plan.scene_instructions["scene_03"]
    assert plan.scene_durations["scene_03"] == 20.0


class _Rewriter(MockLLM):
    """A model that answers with a shorter script."""

    available = True

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete_json(self, prompt: str, **kwargs):  # noqa: ARG002
        self.prompts.append(prompt)
        return {"index": 3, "narration": "压缩过的讲稿。", "segments": []}


def test_with_a_model_the_scene_is_actually_rewritten(settings: Settings, store: ProjectStore):
    project = _project()
    llm = _Rewriter()
    skill = NarrationSkill(SkillContext.build(project, store=store, settings=settings, llm=llm))
    scene = project.scenes[0]

    skill.revise_scene(scene, "第 3 页太长了，压到 20 秒", 20.0)

    assert scene.narration == "压缩过的讲稿。"
    assert scene.duration < 40.0
    # The instruction, the current script and a budget all reach the model —
    # without the budget it has no way to know what "too long" means.
    prompt = llm.prompts[0]
    assert "压到 20 秒" in prompt
    assert "原来的讲稿。" in prompt
    assert "字左右" in prompt


def test_without_a_model_the_scene_is_left_alone_and_it_is_recorded(
    settings: Settings, store: ProjectStore
):
    """Re-voicing the same words would report success for a change that never happened."""
    from doc2video.core import telemetry

    project = _project()
    skill = NarrationSkill(SkillContext.build(project, store=store, settings=settings))
    scene = project.scenes[0]

    with telemetry.run(project.project_id) as recorder:
        skill.revise_scene(scene, "压到 20 秒", 20.0)

    assert scene.narration == "原来的讲稿。"
    assert [d.what for d in recorder.record.degradations] == ["修改第 3 页"]


def test_changing_the_voice_does_not_rewrite_the_script(settings, store):
    """「换个声音」 asks to change the voice, not the video.

    Any intent change used to mean a full revision, so asking for a different
    voice rewrote the script and redirected the camera too — throwing away the
    two things the person was keeping.
    """
    project = VideoProject(
        project_id="proj_revoice",
        source=Source(type=SourceType.PPTX, file="d.pptx", path="source/d.pptx"),
    )
    project.document.pages = [DocumentPage(index=1, title="一", width=1920, height=1080)]
    project.scenes = [
        Scene(scene_id="scn_01", source_page=1, narration="原来的讲稿。", duration=10.0)
    ]

    plan = Planner().edit_plan("换个播音腔的声音", project)

    assert Stage.NARRATE not in plan.stages
    assert Stage.DIRECT not in plan.stages
    assert plan.stages == [Stage.VOICE, Stage.MOTION, Stage.RENDER]
    assert plan.force_voice is True


def test_changing_the_length_still_rewrites(settings, store):
    """The guard is narrow on purpose: length is a property of the words."""
    project = VideoProject(
        project_id="proj_shorter",
        source=Source(type=SourceType.PPTX, file="d.pptx", path="source/d.pptx"),
    )
    project.document.pages = [DocumentPage(index=1, title="一", width=1920, height=1080)]
    project.scenes = [Scene(scene_id="scn_01", source_page=1, narration="原稿。", duration=10.0)]

    plan = Planner().edit_plan("整体压到 3 分钟", project)

    assert Stage.NARRATE in plan.stages
