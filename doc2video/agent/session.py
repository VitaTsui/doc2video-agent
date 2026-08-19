"""Keeping the conversation about one video, and keeping it small.

Append-only beside the project, one turn per line, so a session survives the
process and a crash mid-turn costs one line rather than the file.

Compaction folds the oldest turns into a summary. That is only safe because of
where state lives: the deck, the script, the scenes, the timings and the last
quality report are all in the VideoProject, on disk, complete. The transcript
carries *why* — what was asked, what was tried, what was rejected and on what
grounds — and the next turn re-reads the project rather than replaying the
conversation to reconstruct it.

The rule that keeps that true: **anything the agent must not forget goes into
the project, never only into a turn.** A decision recorded solely in a sentence
is a decision that compaction may drop.
"""

from __future__ import annotations

from pathlib import Path

from ..core.logging import get_logger
from ..schemas.session import Session, Speaker, Turn
from ..tools.llm import LLMTool

log = get_logger(__name__)

SESSION_FILE = "session.jsonl"

# Roughly how much transcript to carry before folding the older half away.
# Generous: compaction costs a model call and loses nuance, so it should be
# rare, not a thing that happens every third turn.
COMPACT_ABOVE = 6000

# Never compact away the immediate past — the turns that explain what the user
# is talking about right now when they say "再短一点".
KEEP_RECENT = 8


class SessionStore:
    """One project's conversation, on disk."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self, project_id: str) -> Session:
        session = Session(project_id=project_id)
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return session

        for line in lines:
            if not line.strip():
                continue
            try:
                turn = Turn.model_validate_json(line)
            except ValueError:
                continue  # a half-written line from a killed process
            session.turns.append(turn)
        session.compacted = sum(1 for t in session.turns if t.speaker is Speaker.SUMMARY)
        return session

    def append(self, turn: Turn) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(turn.model_dump_json() + "\n")
        except OSError as exc:
            # Losing the transcript is bad; losing the render it describes is
            # worse. The project still holds every fact that matters.
            log.debug("无法写入会话：%s", exc)

    def rewrite(self, session: Session) -> None:
        """Replace the file wholesale. Used only by compaction."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            body = "".join(turn.model_dump_json() + "\n" for turn in session.turns)
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(body, encoding="utf-8")
            temporary.replace(self._path)
        except OSError as exc:
            log.debug("无法重写会话：%s", exc)


def compact(session: Session, llm: LLMTool, *, above: int = COMPACT_ABOVE) -> bool:
    """Fold the older turns into one summary. Returns whether anything changed.

    The recent turns are kept verbatim: they are what "再短一点" refers to, and
    a summary of them would answer the wrong question.
    """
    if session.cost() <= above or len(session.turns) <= KEEP_RECENT:
        return False

    older = session.turns[:-KEEP_RECENT]
    recent = session.turns[-KEEP_RECENT:]
    summary = _summarise(older, llm)

    session.turns = [
        Turn(speaker=Speaker.SUMMARY, text=summary, action=f"compacted:{len(older)}"),
        *recent,
    ]
    session.compacted += 1
    return True


def _summarise(turns: list[Turn], llm: LLMTool) -> str:
    """What those turns were about, in a paragraph.

    Falls back to a mechanical digest rather than to nothing: a session that
    silently forgets it forgot is worse than one carrying a crude note, because
    the agent would then answer as if the earlier conversation never happened.
    """
    transcript = "\n".join(f"{turn.speaker.value}：{turn.text}" for turn in turns)
    if llm.available:
        try:
            return llm.complete_text(
                "把下面这段关于「把文档做成讲解视频」的对话压缩成一段话，"
                "保留用户提过的要求、否决过的做法和原因，丢掉过程细节。"
                "视频本身的状态（讲稿、时长、质量分）不用写，那些随时可以重新读到。\n\n"
                + transcript,
                max_tokens=600,
            ).strip()
        except Exception as exc:  # noqa: BLE001 - a crude summary still beats none
            log.warning("压缩会话失败，改用机械摘要：%s", exc)

    asks = [turn.text for turn in turns if turn.speaker is Speaker.USER]
    head = "；".join(ask[:60] for ask in asks[:6])
    return f"（早先的 {len(turns)} 轮已折叠）用户先后提过：{head or '（没有明确要求）'}"
