"""The agent loop: a model deciding what to do next, over deterministic tools.

What this replaces is the honest problem with the old shape. The pipeline ran a
fixed list of eight stages and a regex tried to guess what "第 3 页太长了" meant;
the quality report was computed every run and read by nobody who could act on
it. That is a hand-rolled agent loop that cannot think — and it showed: the edit
path was a no-op for weeks because nothing downstream did anything with what
the regex parsed.

Here the model sees the deck, the script, and the review, and picks the next
action. The actions are exactly the operations that already existed; nothing
here gives it new powers over the machine. It cannot read files, run commands
or reach the network — it can write a script, rewrite one page, ask a question,
or stop.

**Decisions come back as JSON, not as native tool calls.** Five providers back
this, and one of them is a local CLI agent reached over a protocol that carries
a task string and nothing else. A tool-calling loop would work on four and
quietly exclude the one that needs no API key — so every provider answers the
same question in the same shape, and the loop stays one implementation.

Bounded on purpose: each render is minutes of CPU, so an agent free to iterate
is an agent that can spend an afternoon. It gets a small number of steps and a
smaller number of re-renders; running out is reported, not hidden.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..core import ledger
from ..core.logging import get_logger
from ..schemas import VideoProject
from ..schemas.session import Session, Speaker, Turn
from ..tools.llm import LLMTool, model_schema
from .session import SessionStore, compact

log = get_logger(__name__)

# One turn of conversation may cost several renders; this caps the afternoon.
MAX_STEPS = 6
# Re-renders are the expensive part — minutes each — so they are capped tighter
# than the step count.
MAX_RENDERS = 3


class Decision(BaseModel):
    """What to do next, and why.

    ``reason`` is not decoration: it is what the ledger shows the user when the
    agent decides to redo page 7, and the difference between an agent that
    looks capricious and one whose reasoning can be argued with.
    """

    action: Literal["write_script", "revise", "ask", "finish"]
    reason: str
    # write_script: page index (as a string) -> that page's narration.
    narrations: dict[str, str] = Field(default_factory=dict)
    # revise: which scene, and what it should say instead.
    scene_id: str = ""
    narration: str = ""
    # ask / finish: what to say to the person.
    message: str = ""


class Outcome(BaseModel):
    """What one turn of the loop did."""

    reply: str
    steps: int = 0
    renders: int = 0
    stopped_because: str = ""


class AgentLoop:
    """Runs one turn of conversation to its conclusion.

    ``render`` is injected rather than imported so the loop can be tested
    without ffmpeg, and so the caller decides what "render" means — a first
    full pass, or one scene redone.
    """

    def __init__(
        self,
        project: VideoProject,
        llm: LLMTool,
        store: SessionStore,
        *,
        render_all,
        render_scene,
        reload,
    ) -> None:
        self.project = project
        self.llm = llm
        self.store = store
        self._render_all = render_all
        self._render_scene = render_scene
        self._reload = reload

    def run(self, message: str) -> Outcome:
        session = self.store.load(self.project.project_id)
        self._say(session, Speaker.USER, message)

        if not self.llm.available:
            # Without a model there is no loop; say so rather than silently
            # doing the one thing the old pipeline would have done.
            reply = "还没有配模型，我没法自己决定改什么。在下面选一个模型，或者直接把讲稿写给我。"
            self._say(session, Speaker.AGENT, reply)
            return Outcome(reply=reply, stopped_because="no_model")

        if compact(session, self.llm):
            self.store.rewrite(session)
            log.info("会话已压缩，折叠了较早的轮次")

        renders = 0
        for step in range(MAX_STEPS):
            decision = self._decide(session)
            ledger.decision(_ACTION_LABEL.get(decision.action, decision.action), decision.reason)
            self._say(session, Speaker.AGENT, decision.reason, action=decision.action)

            if decision.action == "finish":
                reply = decision.message or "好了。"
                self._say(session, Speaker.AGENT, reply)
                return Outcome(reply=reply, steps=step + 1, renders=renders)

            if decision.action == "ask":
                reply = decision.message or "我需要你再说明一下。"
                self._say(session, Speaker.AGENT, reply)
                return Outcome(
                    reply=reply, steps=step + 1, renders=renders, stopped_because="asked"
                )

            if renders >= MAX_RENDERS:
                reply = (
                    f"这一轮已经重做了 {renders} 次，先停下来听听你的意见——"
                    "再改下去我只是在猜。"
                )
                self._say(session, Speaker.AGENT, reply)
                return Outcome(
                    reply=reply, steps=step + 1, renders=renders, stopped_because="render_budget"
                )

            self._execute(decision, session)
            renders += 1
            self.project = self._reload()

        reply = f"我改了 {renders} 次还没定下来，先交给你看看。"
        self._say(session, Speaker.AGENT, reply)
        return Outcome(
            reply=reply, steps=MAX_STEPS, renders=renders, stopped_because="step_budget"
        )

    # -- the two halves ------------------------------------------------
    def _decide(self, session: Session) -> Decision:
        try:
            answer = self.llm.complete_json(
                self._prompt(session),
                schema=model_schema(Decision),
                system=_SYSTEM,
            )
            return Decision.model_validate(answer)
        except Exception as exc:  # noqa: BLE001 - a broken answer ends the turn, not the app
            log.warning("模型没有给出可用的决定：%s", exc)
            return Decision(
                action="finish",
                reason=f"模型没有给出可用的决定：{exc}",
                message="我没想清楚下一步该做什么，你直接告诉我改哪里吧。",
            )

    def _execute(self, decision: Decision, session: Session) -> None:
        if decision.action == "write_script":
            pages = {int(k): v for k, v in decision.narrations.items() if v.strip()}
            self._render_all(pages)
            self._say(
                session,
                Speaker.TOOL,
                f"按讲稿生成了 {len(pages)} 页",
                action="write_script",
            )
        elif decision.action == "revise":
            self._render_scene(decision.scene_id, decision.narration)
            self._say(
                session,
                Speaker.TOOL,
                f"重做了 {decision.scene_id}",
                action="revise",
            )

    # -- what the model sees -------------------------------------------
    def _prompt(self, session: Session) -> str:
        project = self.project
        lines = [
            f"# 当前工程：{project.document.title or '未命名'}",
            f"目标时长 {project.intent.duration} 秒，受众 {project.intent.audience}，"
            f"语气 {project.intent.tone}",
            "",
        ]

        if project.scenes:
            lines.append("# 现在的成片（逐页）")
            for scene in project.scenes:
                lines.append(
                    f"- {scene.scene_id}（第 {scene.source_page} 页，{scene.duration:.1f}s）："
                    f"{scene.narration[:120]}"
                )
        else:
            lines.append("# 还没有讲稿。下面是各页内容和字数预算")
            from ..skills import NarrationSkill
            from ..skills.base import SkillContext

            guide = {
                row["page"]: row
                for row in NarrationSkill(SkillContext.build(project, llm=self.llm)).guide()
            }
            for page in project.document.ordered_pages():
                budget = guide.get(page.index)
                lines.append(
                    f"- 第 {page.index} 页｜{page.page_type.value}｜{page.title or '无标题'}"
                    + (f"｜约 {budget['target_chars']} 字" if budget else "")
                )
                if page.key_points:
                    lines.append("    要点：" + "；".join(page.key_points))

        if project.quality:
            lines += ["", f"# 上次质检：{project.quality.score} 分"]
            lines += [
                f"- [{f.severity}] {f.scene_id or '整体'}：{f.message}" for f in project.review
            ]

        lines += ["", "# 对话"]
        lines += [f"{turn.speaker.value}：{turn.text}" for turn in session.recent(12)]
        return "\n".join(lines)

    def _say(self, session: Session, speaker: Speaker, text: str, action: str = "") -> None:
        turn = Turn(speaker=speaker, text=text, action=action)
        session.turns.append(turn)
        self.store.append(turn)


_ACTION_LABEL = {
    "write_script": "决定写讲稿",
    "revise": "决定重做某一页",
    "ask": "决定先问清楚",
    "finish": "决定收工",
}

_SYSTEM = """你在把一份演示文档做成讲解视频。你能做的只有四件事：

- write_script：为所有页写讲稿，然后配音、设计镜头、渲染、质检。第一次必须走这一步。
- revise：只重做一页。改动只影响这一页，其余片段直接复用。
- ask：需要用户说明才能继续时，问一句。
- finish：告诉用户结果，结束这一轮。

判断依据：

1. 字数预算是硬约束。时长由字数估算而来，音频一旦生成长度就改不动，只能重写重配。
2. 质检里的 warning 值得处理，但不必全部消灭。「重合度高、接近照读」说明那一页在念
   页面文字，值得重写；「实际时长与目标偏差大」要看偏多少，差一两成不值得为它重做全片。
3. 每重做一次都要几分钟。改一页就用 revise，不要整体重来。
4. 想不清楚该改什么的时候，用 ask 问，而不是猜着改。

reason 会原样显示给用户，写清楚你为什么这么决定。"""
