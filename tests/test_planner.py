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


def test_an_empty_script_still_asks_for_a_render(settings, store):
    """`{}` is a script, `None` is the absence of one.

    Both are falsy, and the desktop app sends `{}` whenever 开始生成 is pressed
    with nothing typed — a case its own copy promises will render with
    placeholder text. Reading it as "no script" routed the call into the edit
    branch, which matched no rule, skipped narration and died at render.
    """
    from doc2video.agent import Doc2VideoAgent

    agent = Doc2VideoAgent(settings, store)
    project = _project()

    empty = agent._plan_for(
        "按调用方讲稿生成视频", project, narrations={}, scene_narrations=None, editing=True
    )
    assert Stage.NARRATE in empty.stages
    assert Stage.RENDER in empty.stages

    # The whole deck, not whatever the message happened to parse as.
    assert empty.scene_ids == []

    absent = agent._plan_for(
        "第 7 页太长了", project, narrations=None, scene_narrations=None, editing=True
    )
    assert absent.scene_ids == ["scene_07"]


def test_reading_the_deck_and_writing_it_are_two_steps(settings, store):
    """The parse must not wait behind the words.

    Parsing takes seconds and writing takes as long as the model takes. Folded
    into one step the deck appears only once the whole script is written, and
    the wait is spent looking at nothing; split, the pages are on screen first
    and fill in as they are written.
    """
    from doc2video.agent import Doc2VideoAgent

    agent = Doc2VideoAgent(settings, store)

    reading = agent.planner.prepare_plan("讲三分钟", _project())
    assert reading.stages == [Stage.PARSE, Stage.UNDERSTAND]

    writing = agent.planner.draft_plan()
    assert writing.stages == [Stage.NARRATE]
    # And it stops at the script: nothing is voiced or rendered from a draft
    # nobody has looked at yet.
    assert Stage.VOICE not in writing.stages
    assert Stage.RENDER not in writing.stages

    # The deck is not re-read to write against it — it was read in step one.
    assert Stage.PARSE not in writing.stages


def test_the_tone_knob_is_connected_to_something():
    """`tone` was read by the prompt and written by nothing.

    Every deck ever made came out 「清晰、稳重」 however it was asked for,
    because the field existed, the prompt printed it, and no code path
    assigned it. `style` had the mirror-image problem: parsed, stored, and
    never put in front of the model.
    """
    lively = parse_intent_rules("活泼一点，讲给年轻人听", VideoIntent())
    assert lively.style == "lively"
    assert lively.tone != VideoIntent().tone

    # An explicit tone is a correction of the one the style implied.
    both = parse_intent_rules("专业一点，但语气亲切些", VideoIntent())
    assert both.style == "professional"
    assert "亲切" in both.tone

    from doc2video.skills.narration import STYLE_BRIEF

    assert set(STYLE_BRIEF) >= {"professional", "tech", "lively", "casual", "formal"}


def test_asking_for_a_slower_voice_is_understood():
    """Voice and speed belong to the video, so they can be said rather than configured."""
    slower = parse_intent_rules("语速慢一点", VideoIntent())
    assert 0 < slower.speech_rate < 1

    # The longer phrase wins over the shorter one it contains.
    much_slower = parse_intent_rules("再慢一点", VideoIntent())
    assert much_slower.speech_rate < slower.speech_rate

    assert parse_intent_rules("快一点", VideoIntent()).speech_rate > 1
    assert parse_intent_rules("按默认来", VideoIntent()).speech_rate == 0


def test_a_length_typed_in_chinese_numerals_is_a_length():
    """「七分钟」 is not an exotic phrasing — it is how the sentence gets typed.

    Reading only 「7分钟」 left the duration at its 480-second default, and the
    window then reported 「按这个要求算下来大约 480 秒」: the request had been
    dropped and the confirmation claimed to have honoured it. Wrong in the one
    direction that cannot be noticed until the video is finished.
    """
    said = {
        "做一个七分钟左右的讲解视频": 420,
        "控制在五分半": 330,
        "十分钟": 600,
        "二十五分钟以内": 1500,
        "两分钟": 120,
        "九十秒": 90,
        "半分钟": 30,
        "半小时": 1800,
        "一个小时": 3600,
        # 「5 分 30 秒」 read as 秒 alone is a 30-second video — further from the
        # request than not understanding it at all.
        "压到 5 分 30 秒": 330,
        "五分三十秒": 330,
    }
    for message, seconds in said.items():
        assert parse_intent_rules(message, VideoIntent()).duration == seconds, message


def test_a_number_that_is_not_a_length_stays_out_of_it():
    """分 is too common a character to key on by itself."""
    for message in ("这一页十分重要", "把第二部分讲透", "讲清楚就行"):
        assert parse_intent_rules(message, VideoIntent(duration=480)).duration == 480, message


def test_the_default_is_not_something_the_user_asked_for():
    """The window credits the number to the user; it may only do that when it is theirs."""
    from doc2video.agent.planner import stated_duration

    assert stated_duration("做一个七分钟左右的讲解视频") == 420
    assert stated_duration("讲清楚就行") is None
    assert stated_duration("") is None


def test_a_term_the_engine_cuts_in_half_can_be_named_as_one_word():
    """The synthesiser picks its own phrase boundaries and gets them wrong.

    Measured on 「国家人工智能应用中试基地」: `say` stops 0.27 seconds after 中,
    reading 「应用中」 as a phrase and stranding 「试基地」. Long enough to hear a
    word being cut in half, and nothing in the script says to pause there.

    The fix is a boundary in front of the term, which is what the spoken form
    carries. The caption keeps the term as written.
    """
    from doc2video.agent.planner import _pronunciations_in

    assert _pronunciations_in("中试基地是一个词") == {"中试基地": " 中试基地"}
    assert _pronunciations_in("「应用中试」别断开") == {"应用中试": " 应用中试"}
    assert _pronunciations_in("RAG 念 R A G") == {"RAG": "R A G"}
    assert _pronunciations_in("把第 3 页压到 20 秒") == {}


def test_how_a_term_is_said_can_be_learned_after_hearing_it():
    """Which is the only time anyone finds out.

    This path read no dictionary at all, so 「RAG 念 R A G」 worked only in the
    very first message — before there was a film to hear it in.
    """
    project = _project()
    plan = parse_edit_rules("中试基地是一个词", project)

    assert plan.intent.pronunciation == {"中试基地": " 中试基地"}
    # And it has to be spoken again: the words did not change, the sound did.
    assert plan.revoice
