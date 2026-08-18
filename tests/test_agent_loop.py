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
