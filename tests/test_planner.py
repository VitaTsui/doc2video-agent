"""Rule-based intent and edit parsing — the offline path of the planner."""

from __future__ import annotations

from doc2video.agent.planner import Planner, Stage, parse_edit_rules, parse_intent_rules
from doc2video.schemas import Scene, Source, SourceType, VideoIntent, VideoProject


def _project() -> VideoProject:
    return VideoProject(
        project_id="proj_test",
        source=Source(type=SourceType.PPTX, file="d.pptx", path="source/d.pptx", page_count=12),
        intent=VideoIntent(duration=480),
        scenes=[
            Scene(scene_id="scene_01", source_page=1, narration="一", duration=10),
            Scene(scene_id="scene_07", source_page=7, narration="七", duration=40),
        ],
    )


def test_parse_intent_reads_duration_and_audience():
    intent = parse_intent_rules(
        "帮我生成一个8分钟的产品讲解视频，面向企业客户，专业一点", VideoIntent()
    )
    assert intent.duration == 480
    assert intent.audience == "企业客户"
    assert intent.style == "professional"


def test_parse_intent_reads_emphasis_page_range():
    intent = parse_intent_rules("第5~8页重点讲，关键数据出现时放大", VideoIntent())
    assert intent.emphasis_pages == [5, 6, 7, 8]
    assert intent.zoom_on_key_data is True


def test_parse_intent_keeps_unmentioned_fields():
    current = VideoIntent(duration=300, audience="投资人", style="tech")
    intent = parse_intent_rules("再短一点吧", current)
    assert intent.duration == 300
    assert intent.audience == "投资人"


def test_parse_edit_scopes_to_the_mentioned_page():
    plan = parse_edit_rules("第7页太长了，压缩到20秒", _project())
    assert [e.scene_id for e in plan.scene_edits] == ["scene_07"]
    assert plan.scene_edits[0].target_duration == 20
    # A per-page duration must not be mistaken for the whole video's duration.
    assert plan.intent.duration == 480


def test_parse_edit_global_duration_change():
    plan = parse_edit_rules("压缩到6分钟", _project())
    assert plan.intent.duration == 360
    assert plan.scene_edits == []


def test_parse_edit_detects_revoice_and_redirect():
    assert parse_edit_rules("声音更年轻一些", _project()).revoice is True
    assert parse_edit_rules("所有关键数字都放大", _project()).redirect is True


def test_execution_plan_for_scene_edit_is_narrow():
    planner = Planner()
    plan = planner.edit_plan("第7页太长了，压缩到20秒", _project())

    assert plan.scene_ids == ["scene_07"]
    assert plan.scene_durations["scene_07"] == 20
    assert Stage.PARSE not in plan.stages
    assert Stage.UNDERSTAND not in plan.stages
    assert Stage.RENDER in plan.stages


def test_execution_plan_for_camera_only_change_skips_narration():
    planner = Planner()
    plan = planner.edit_plan("所有关键数字都放大", _project())

    assert Stage.NARRATE not in plan.stages
    assert Stage.DIRECT in plan.stages
