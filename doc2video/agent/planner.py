"""Planner — natural language in, an execution plan out.

Two jobs (方案 §5): turn a first request into a Video Intent, and turn a
follow-up chat message into the smallest set of stages and scenes that have to
be redone. Rules parse the brief; nothing here calls a model.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field

from ..core.logging import get_logger
from ..schemas import VideoIntent, VideoProject

log = get_logger(__name__)


class Stage(StrEnum):
    PARSE = "parse"
    UNDERSTAND = "understand"
    NARRATE = "narrate"
    VOICE = "voice"
    DIRECT = "direct"
    MOTION = "motion"
    RENDER = "render"
    REVIEW = "review"


FULL_PIPELINE = [
    Stage.PARSE,
    Stage.UNDERSTAND,
    Stage.NARRATE,
    Stage.VOICE,
    Stage.DIRECT,
    Stage.MOTION,
    Stage.RENDER,
    Stage.REVIEW,
]


# A content edit invalidates everything downstream of the script, but never the
# parse/understand stages — the document itself did not change.
POST_SCRIPT_STAGES = [
    Stage.NARRATE,
    Stage.VOICE,
    Stage.DIRECT,
    Stage.MOTION,
    Stage.RENDER,
    Stage.REVIEW,
]

REVISION_STAGES = [
    Stage.NARRATE,
    Stage.VOICE,
    Stage.DIRECT,
    Stage.MOTION,
    Stage.RENDER,
    Stage.REVIEW,
]


class SceneEdit(BaseModel):
    scene_id: str
    instruction: str
    target_duration: float = Field(default=0.0, description="秒；0 表示不改变时长")


class EditPlan(BaseModel):
    """What a follow-up message asks for, in terms this system can execute."""

    summary: str
    intent: VideoIntent
    scene_edits: list[SceneEdit]
    rewrite_all: bool
    revoice: bool
    redirect: bool


class ExecutionPlan(BaseModel):
    summary: str = ""
    stages: list[Stage] = Field(default_factory=list)
    scene_ids: list[str] = Field(default_factory=list)
    # Caller-written script: page index -> text for a full run, scene id -> text
    # for a targeted edit. Empty means "no script supplied" (placeholder path).
    narrations: dict[int, str] = Field(default_factory=dict)
    scene_narrations: dict[str, str] = Field(default_factory=dict)
    # Seconds a scene was asked to become. Informational: nothing here writes to
    # a length any more, but the caller needs the number to rewrite against it.
    scene_durations: dict[str, float] = Field(default_factory=dict)
    intent: VideoIntent | None = None
    force_voice: bool = False


class Planner:
    """Turns a one-line brief into an intent and a stage list, by rule.

    Rules, not a model: this service holds none. They read the things a brief
    actually states — a duration, an audience, which pages matter — and leave
    everything they cannot parse at its default rather than inventing it.
    """

    # -- first run --------------------------------------------------------
    def initial_intent(self, message: str, *, page_count: int, current: VideoIntent) -> VideoIntent:
        return self._sanitize_intent(parse_intent_rules(message, current), page_count)

    def prepare_plan(self, message: str, project: VideoProject) -> ExecutionPlan:
        """Parse and understand only — stop before anything needs a script."""
        intent = self.initial_intent(
            message, page_count=project.source.page_count, current=project.intent
        )
        return ExecutionPlan(
            summary="解析文档，等待讲稿",
            stages=[Stage.PARSE, Stage.UNDERSTAND],
            intent=intent,
        )

    def render_plan(self, narrations: dict[int, str]) -> ExecutionPlan:
        """Everything downstream of a script: adopt it, then voice and render.

        A distinct plan rather than a parsed one — "here is the script, render
        it" is a statement about what the caller supplied, not an instruction to
        interpret, and routing it through the edit rules would leave it matching
        nothing and skipping the narration stage entirely.
        """
        return ExecutionPlan(
            summary="按调用方讲稿生成视频",
            stages=list(POST_SCRIPT_STAGES),
            narrations=dict(narrations),
        )

    def initial_plan(self, message: str, project: VideoProject) -> ExecutionPlan:
        intent = self.initial_intent(
            message, page_count=project.source.page_count, current=project.intent
        )
        return ExecutionPlan(
            summary="解析文档并生成完整视频",
            stages=list(FULL_PIPELINE),
            intent=intent,
        )

    # -- follow-up edits ---------------------------------------------------
    def edit_plan(self, message: str, project: VideoProject) -> ExecutionPlan:
        return self._to_execution_plan(parse_edit_rules(message, project), project)

    def _to_execution_plan(self, plan: EditPlan, project: VideoProject) -> ExecutionPlan:
        known = {s.scene_id for s in project.scenes}
        edits = [e for e in plan.scene_edits if e.scene_id in known]

        intent = self._sanitize_intent(plan.intent, project.source.page_count)
        intent_changed = intent.model_dump() != project.intent.model_dump()

        stages: list[Stage] = []
        scene_ids: list[str] = []

        if plan.rewrite_all or (intent_changed and not edits):
            stages = REVISION_STAGES
        elif edits:
            scene_ids = [e.scene_id for e in edits]
            stages = REVISION_STAGES
        elif plan.redirect:
            stages = [Stage.DIRECT, Stage.MOTION, Stage.RENDER, Stage.REVIEW]
        elif plan.revoice:
            stages = [Stage.VOICE, Stage.MOTION, Stage.RENDER, Stage.REVIEW]
        else:
            stages = [Stage.MOTION, Stage.RENDER]

        return ExecutionPlan(
            summary=plan.summary or "按用户指令修改工程",
            stages=stages,
            scene_ids=scene_ids,
            scene_durations={
                e.scene_id: e.target_duration for e in edits if e.target_duration
            },
            intent=intent if intent_changed else None,
            force_voice=plan.revoice,
        )

    # -- bounds -------------------------------------------------------------
    @staticmethod
    def _sanitize_intent(intent: VideoIntent, page_count: int) -> VideoIntent:
        intent.duration = max(15, min(7200, int(intent.duration)))
        limit = page_count or 9999
        intent.emphasis_pages = sorted({p for p in intent.emphasis_pages if 1 <= p <= limit})
        intent.skip_pages = sorted({p for p in intent.skip_pages if 1 <= p <= limit})
        return intent




# --------------------------------------------------------------------------
# Rule-based fallbacks — used when no LLM is configured, and as a safety net.
# --------------------------------------------------------------------------

_MINUTES = re.compile(r"(\d+(?:\.\d+)?)\s*分钟")
_SECONDS = re.compile(r"(\d+(?:\.\d+)?)\s*秒")
_PAGE = re.compile(r"第\s*(\d+)\s*[页頁]")
_PAGE_RANGE = re.compile(r"第?\s*(\d+)\s*[~-—到至]\s*(\d+)\s*[页頁]")

_STYLE_HINTS = {
    "专业": "professional",
    "科技": "tech",
    "活泼": "lively",
    "轻松": "casual",
    "正式": "formal",
}


def parse_intent_rules(message: str, current: VideoIntent) -> VideoIntent:
    """Regex intent parsing — covers the phrasings users actually type."""
    intent = current.model_copy(deep=True)

    if m := _MINUTES.search(message):
        intent.duration = int(float(m.group(1)) * 60)
    elif m := _SECONDS.search(message):
        intent.duration = int(float(m.group(1)))

    for keyword, style in _STYLE_HINTS.items():
        if keyword in message:
            intent.style = style
            break

    if "企业客户" in message or "客户" in message:
        intent.audience = "企业客户"
    elif "开发者" in message or "技术" in message:
        intent.audience = "技术人员"
    elif "投资" in message:
        intent.audience = "投资人"

    emphasis: set[int] = set(intent.emphasis_pages)
    if "重点" in message or "详细" in message:
        for start, end in _PAGE_RANGE.findall(message):
            emphasis.update(range(int(start), int(end) + 1))
        if not _PAGE_RANGE.search(message):
            emphasis.update(int(p) for p in _PAGE.findall(message))
    intent.emphasis_pages = sorted(emphasis)

    if "放大" in message and ("数字" in message or "数据" in message):
        intent.zoom_on_key_data = True

    intent.instructions = message.strip()[:500]
    return intent


def parse_edit_rules(message: str, project: VideoProject) -> EditPlan:
    """Regex edit parsing. Deliberately conservative: when a message cannot be
    mapped confidently, do the cheapest safe thing rather than rewriting everything."""
    intent = project.intent.model_copy(deep=True)
    scene_edits: list[SceneEdit] = []

    pages = [int(p) for p in _PAGE.findall(message)]
    mentions_page = bool(pages)

    target_seconds = 0.0
    if m := _SECONDS.search(message):
        target_seconds = float(m.group(1))
    elif m := _MINUTES.search(message):
        minutes = float(m.group(1))
        if mentions_page:
            target_seconds = minutes * 60
        else:
            intent.duration = int(minutes * 60)

    if mentions_page:
        for page_index in pages:
            scene = project.scene_by_page(page_index)
            if scene is None:
                continue
            scene_edits.append(
                SceneEdit(
                    scene_id=scene.scene_id,
                    instruction=message.strip(),
                    target_duration=target_seconds,
                )
            )
    elif target_seconds and not _MINUTES.search(message):
        intent.duration = int(target_seconds)

    revoice = any(k in message for k in ("音色", "声音", "配音", "语速", "换个声音"))
    redirect = any(k in message for k in ("放大", "高亮", "镜头", "聚焦", "指针"))
    rewrite_all = any(k in message for k in ("整体重写", "全部重写", "重写讲稿"))

    summary_bits = []
    if scene_edits:
        summary_bits.append(f"修改 {len(scene_edits)} 个场景")
    if intent.duration != project.intent.duration:
        summary_bits.append(f"总时长调整为 {intent.duration} 秒")
    if revoice:
        summary_bits.append("重新配音")
    if redirect:
        summary_bits.append("重做镜头")

    return EditPlan(
        summary="；".join(summary_bits) or "按指令更新工程并重新渲染",
        intent=intent,
        scene_edits=scene_edits,
        rewrite_all=rewrite_all,
        revoice=revoice,
        redirect=redirect,
    )
