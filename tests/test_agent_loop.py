"""The loop that replaced a regex and a fixed pipeline.

What is worth pinning here is not the happy path — it is every way the loop can
run away or go quiet. An agent that renders until the afternoon is gone, or one
that fails silently because a model returned nonsense, is worse than the fixed
pipeline it replaced.
"""

from __future__ import annotations

from pathlib import Path

from doc2video.agent.loop import MAX_RENDERS, AgentLoop, Decision
from doc2video.agent.session import SESSION_FILE, SessionStore, compact
from doc2video.schemas import Source, SourceType, VideoProject
from doc2video.schemas.session import Session, Speaker, Turn
from doc2video.tools.llm import MockLLM


class _Scripted(MockLLM):
    """A model that answers with the decisions handed to it, in order."""

    available = True

    def __init__(self, *decisions: Decision) -> None:
        self._decisions = list(decisions)
        self.prompts: list[str] = []

    def complete_json(self, prompt: str, **kwargs):  # noqa: ARG002
        self.prompts.append(prompt)
        if not self._decisions:
            return Decision(action="finish", reason="没有更多决定", message="完成").model_dump()
        return self._decisions.pop(0).model_dump()

    def complete_text(self, prompt: str, **kwargs) -> str:  # noqa: ARG002
        return "（摘要）"


def _project() -> VideoProject:
    return VideoProject(
        project_id="proj_loop",
        source=Source(type=SourceType.PPTX, file="d.pptx", path="source/d.pptx", page_count=3),
    )


def _loop(tmp_path: Path, llm, renders: list) -> AgentLoop:
    project = _project()
    return AgentLoop(
        project,
        llm,
        SessionStore(tmp_path / SESSION_FILE),
        render_all=lambda n: renders.append(("all", n)),
        render_scene=lambda s, n: renders.append(("scene", s, n)),
        revoice=lambda voice, rate: renders.append(("voice", voice, rate)),
        write_all=lambda: renders.append(("write",)),
        reload=lambda: project,
    )


def test_it_writes_a_script_then_stops_when_the_model_says_so(tmp_path: Path):
    renders: list = []
    llm = _Scripted(
        Decision(action="write_script", reason="还没有讲稿", narrations={"1": "第一页"}),
        Decision(action="finish", reason="质检没问题", message="好了"),
    )
    outcome = _loop(tmp_path, llm, renders).run("做一个 2 分钟的视频")

    assert renders == [("all", {1: "第一页"})]
    assert outcome.reply == "好了"
    assert outcome.renders == 1


def test_it_cannot_re_render_all_afternoon(tmp_path: Path):
    """Each render is minutes of CPU; an unbounded loop is an afternoon gone."""
    renders: list = []
    forever = [
        Decision(action="revise", reason="还能更好", scene_id=f"scene_{i:02d}", narration="改")
        for i in range(20)
    ]
    outcome = _loop(tmp_path, _Scripted(*forever), renders).run("再打磨一下")

    assert len(renders) == MAX_RENDERS
    assert outcome.stopped_because == "render_budget"
    # And it says so, rather than appearing to have finished normally.
    assert str(MAX_RENDERS) in outcome.reply


def test_a_nonsense_answer_ends_the_turn_instead_of_the_app(tmp_path: Path):
    class Broken(MockLLM):
        available = True

        def complete_json(self, prompt: str, **kwargs):  # noqa: ARG002
            return {"action": "explode", "reason": "?"}

    renders: list = []
    outcome = _loop(tmp_path, Broken(), renders).run("做个视频")

    assert renders == []
    assert outcome.reply  # something is said to the user
    assert outcome.steps == 1


def test_without_a_model_it_says_so_rather_than_guessing(tmp_path: Path):
    """The old shape guessed with a regex; saying "I cannot" beats guessing."""
    renders: list = []
    outcome = _loop(tmp_path, MockLLM(), renders).run("第 3 页太长了")

    assert renders == []
    assert outcome.stopped_because == "no_model"
    assert "模型" in outcome.reply


def test_asking_ends_the_turn_and_waits(tmp_path: Path):
    renders: list = []
    llm = _Scripted(Decision(action="ask", reason="不知道要多长", message="想要几分钟？"))
    outcome = _loop(tmp_path, llm, renders).run("帮我改改")

    assert outcome.reply == "想要几分钟？"
    assert outcome.stopped_because == "asked"
    assert renders == []


def test_the_conversation_survives_the_process(tmp_path: Path):
    renders: list = []
    llm = _Scripted(Decision(action="finish", reason="没事做", message="好"))
    _loop(tmp_path, llm, renders).run("你好")

    reloaded = SessionStore(tmp_path / SESSION_FILE).load("proj_loop")
    assert [t.speaker for t in reloaded.turns][0] is Speaker.USER
    assert reloaded.turns[0].text == "你好"


# -- compaction ------------------------------------------------------------
def test_compaction_keeps_the_recent_turns_verbatim():
    """"再短一点" refers to the turns just before it; a summary of those would
    answer the wrong question."""
    session = Session(project_id="p")
    session.turns = [Turn(speaker=Speaker.USER, text="x" * 400) for _ in range(40)]
    session.turns.append(Turn(speaker=Speaker.USER, text="再短一点"))

    assert compact(session, _Scripted(), above=1000) is True
    assert session.turns[0].speaker is Speaker.SUMMARY
    assert session.turns[-1].text == "再短一点"
    assert session.compacted == 1


def test_a_short_conversation_is_left_alone():
    session = Session(project_id="p")
    session.turns = [Turn(speaker=Speaker.USER, text="做个视频")]
    assert compact(session, _Scripted()) is False


def test_compaction_without_a_model_still_records_that_it_happened():
    """A session that silently forgets it forgot is worse than one carrying a
    crude note: the agent would answer as if the earlier turns never existed."""
    session = Session(project_id="p")
    session.turns = [Turn(speaker=Speaker.USER, text="改短一点" * 50) for _ in range(30)]

    assert compact(session, MockLLM(), above=500) is True
    assert "折叠" in session.turns[0].text


def test_decisions_land_in_the_ledger_alongside_the_renders(tmp_path: Path) -> None:
    """The reasoning and the work it caused must read as one sequence.

    They are recorded from different places — the loop writes decisions, the
    executor writes stages — and for a while nothing recorded the decisions at
    all, because the only recorder was opened inside a render. A decision that
    exists only in a log the user cannot see is not an account of anything.
    """
    from doc2video.core import ledger

    book = tmp_path / "ledger.jsonl"
    loop = AgentLoop(
        _project(),
        _Scripted(
            Decision(
                action="write_script", reason="还没有讲稿，先写一版", narrations={"1": "开场"}
            ),
            Decision(action="finish", reason="质检没问题", message="好了"),
        ),
        SessionStore(tmp_path / SESSION_FILE),
        # Each render opens a recorder of its own, exactly as the service does.
        render_all=lambda pages: _record_a_render(book),  # noqa: ARG005
        render_scene=lambda scene_id, narration: None,  # noqa: ARG005
        revoice=lambda voice, rate: None,  # noqa: ARG005
        reload=_project,
    )

    with ledger.recording(book):
        loop.run("帮我做一版")

    entries = ledger.read(book)
    assert [e.kind.value for e in entries] == ["decision", "stage", "decision"]
    assert entries[0].detail == "还没有讲稿，先写一版"
    # Numbered once, in order, despite two recorders having been open.
    assert [e.seq for e in entries] == [1, 2, 3]


def _record_a_render(book: Path) -> None:
    from doc2video.core import ledger

    with ledger.recording(book, run_id="run_1") as recorder, recorder.stage("渲染"):
        pass


def test_a_finished_turn_can_be_read_back_from_disk(tmp_path: Path) -> None:
    """What the window replays when it reopens.

    The loop's own turns are the record: the user's message, the reasoning
    behind each decision, what the tools did, and the reply. Only the last of
    those belongs on screen — the rest is the ledger's job — so the shape has
    to survive the round trip well enough to tell them apart.
    """
    store = SessionStore(tmp_path / SESSION_FILE)
    loop = AgentLoop(
        _project(),
        _Scripted(Decision(action="ask", reason="不知道给谁看", message="这是给客户还是内部？")),
        store,
        render_all=lambda pages: None,  # noqa: ARG005
        render_scene=lambda scene_id, narration: None,  # noqa: ARG005
        revoice=lambda voice, rate: None,  # noqa: ARG005
        reload=_project,
    )
    loop.run("帮我做个视频")

    reloaded = store.load("proj_loop")
    assert [(t.speaker.value, bool(t.action)) for t in reloaded.turns] == [
        ("user", False),
        ("agent", True),  # the reasoning, which belongs in the ledger
        ("agent", False),  # the reply, which belongs on screen
    ]
    assert reloaded.turns[-1].text == "这是给客户还是内部？"


def test_a_script_keyed_by_scene_still_lands_on_the_right_page(tmp_path: Path):
    """The prompt shows the video as `scn_04（第 4 页…）`, so models key by scene.

    Read as page numbers, that ended the turn with
    `invalid literal for int(): 'scn_04'` — the whole request lost to the shape
    of a dictionary key.
    """
    from doc2video.schemas import Scene

    renders: list = []
    loop = _loop(
        tmp_path,
        _Scripted(
            Decision(
                action="write_script",
                reason="按质检改两页",
                narrations={"scn_02": "第二页新讲稿。", "3": "第三页新讲稿。"},
            ),
            Decision(action="finish", reason="改完了", message="好了"),
        ),
        renders,
    )
    loop.project.scenes = [
        Scene(scene_id="scn_02", source_page=2, narration="旧的", duration=5.0),
    ]

    loop.run("按质检改一下")

    assert renders == [("all", {2: "第二页新讲稿。", 3: "第三页新讲稿。"})]


def test_a_key_that_names_nothing_is_dropped_rather_than_ending_the_turn(tmp_path: Path):
    renders: list = []
    loop = _loop(
        tmp_path,
        _Scripted(
            Decision(
                action="write_script",
                reason="写讲稿",
                narrations={"封面": "开场白。", "1": "第一页。"},
            ),
            Decision(action="finish", reason="写完了", message="好了"),
        ),
        renders,
    )

    loop.run("写一版")

    assert renders == [("all", {1: "第一页。"})]


def test_changing_the_voice_does_not_go_near_the_script(tmp_path: Path):
    """「把语音换成 Yunyang」 is not a request to rewrite anything.

    With no action for it, the model reached for the only one that touched
    audio — `write_script` — which rewrites the words somebody approved.
    """
    renders: list = []
    loop = _loop(
        tmp_path,
        _Scripted(
            Decision(
                action="retune",
                reason="用户只要换音色，讲稿不动",
                voice="zh-CN-YunyangNeural",
            ),
            Decision(action="finish", reason="换好了", message="换成播音腔了"),
        ),
        renders,
    )

    loop.run("把语音换成zh-CN-YunyangNeural")

    assert renders == [("voice", "zh-CN-YunyangNeural", 0.0)]


def test_an_empty_write_script_hands_the_writing_to_the_skill(tmp_path: Path):
    """Writing thirty pages in one answer is worse than writing them one by one.

    Measured on the same 30-page deck: the narration skill wrote 2135–2483
    characters, a model asked for the whole deck at once wrote 1800 twice —
    and its pages carried the AI tics the writing prompt exists to keep out,
    because that path never reads it.
    """
    renders: list = []
    loop = _loop(
        tmp_path,
        _Scripted(
            Decision(action="write_script", reason="还没有讲稿，先写一版"),
            Decision(action="finish", reason="做完了", message="好了"),
        ),
        renders,
    )

    loop.run("帮我做一版")

    assert renders == [("write",)]


def test_naming_pages_still_replaces_exactly_those(tmp_path: Path):
    """The other half of the same action: rewriting the pages it names."""
    renders: list = []
    loop = _loop(
        tmp_path,
        _Scripted(
            Decision(action="write_script", reason="改第一页", narrations={"1": "新的开场。"}),
            Decision(action="finish", reason="改完了", message="好了"),
        ),
        renders,
    )

    loop.run("第一页重写")

    assert renders == [("all", {1: "新的开场。"})]


def test_the_brief_is_kept_where_it_was_said(tmp_path: Path, settings, store):
    """Reopening a project has to show the conversation that made it.

    The transcript used to be written only by the chat loop, and the main path
    never goes through it: a project made by dropping a deck and pressing the
    buttons had no transcript at all, or one whose only 「用户」 line was a
    sentence the window had sent on its own behalf. The one thing the person
    really wrote — what they asked for when they dropped the file — was the
    thing that went missing.
    """
    from doc2video.agent.service import Doc2VideoAgent

    service = Doc2VideoAgent(settings=settings, store=store)
    project = service.create_project(_deck(tmp_path))
    service._remember(project.project_id, Speaker.USER, "讲给技术同事听，两分钟左右")

    turns = SessionStore(store.project_dir(project.project_id) / SESSION_FILE).load(
        project.project_id
    ).turns
    assert [(t.speaker.value, t.text) for t in turns] == [
        ("user", "讲给技术同事听，两分钟左右")
    ]

    # Nothing typed is nothing said: an empty brief must not become a turn.
    service._remember(project.project_id, Speaker.USER, "   ")
    assert len(
        SessionStore(store.project_dir(project.project_id) / SESSION_FILE)
        .load(project.project_id)
        .turns
    ) == 1


def _deck(tmp_path: Path) -> Path:
    """The smallest thing `create_project` will accept as a source."""
    path = tmp_path / "d.pptx"
    path.write_bytes(b"PK\x03\x04")
    return path
