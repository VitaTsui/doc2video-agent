"""Planner — natural language in, an execution plan out.

Two jobs (方案 §5): turn a first request into a Video Intent, and turn a
follow-up chat message into the smallest set of stages and scenes that have to
be redone. The LLM proposes; deterministic rules validate and bound the result.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field

from ..core.logging import get_logger
from ..schemas import VideoIntent, VideoProject
from ..skills.base import load_prompt
from ..tools.llm import LLMTool, model_schema

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
_EDIT_STAGES = [
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
    scene_instructions: dict[str, str] = Field(default_factory=dict)
    scene_durations: dict[str, float] = Field(default_factory=dict)
    intent: VideoIntent | None = None
    force_voice: bool = False


class Planner:
    def __init__(self, llm: LLMTool) -> None:
        self.llm = llm

    # -- first run --------------------------------------------------------
    def initial_intent(self, message: str, *, page_count: int, current: VideoIntent) -> VideoIntent:
        if self.llm.available:
            try:
                raw = self.llm.complete_json(
                    self._intent_prompt(message, current, page_count),
                    schema=model_schema(VideoIntent),
                    system=load_prompt("intent"),
                )
                intent = VideoIntent.model_validate(raw)
                return self._sanitize_intent(intent, page_count)
            except Exception as exc:
                log.warning("意图解析失败（%s），改用规则解析", exc)
        return self._sanitize_intent(parse_intent_rules(message, current), page_count)

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
        plan = None
        if self.llm.available:
            try:
                raw = self.llm.complete_json(
                    self._edit_prompt(message, project),
                    schema=model_schema(EditPlan),
                    system=_EDIT_SYSTEM,
                )
                plan = EditPlan.model_validate(raw)
            except Exception as exc:
                log.warning("修改意图解析失败（%s），改用规则解析", exc)
        if plan is None:
            plan = parse_edit_rules(message, project)
        return self._to_execution_plan(plan, project)

    def _to_execution_plan(self, plan: EditPlan, project: VideoProject) -> ExecutionPlan:
        known = {s.scene_id for s in project.scenes}
        edits = [e for e in plan.scene_edits if e.scene_id in known]

        intent = self._sanitize_intent(plan.intent, project.source.page_count)
        intent_changed = intent.model_dump() != project.intent.model_dump()

        stages: list[Stage] = []
        scene_ids: list[str] = []

        if plan.rewrite_all or (intent_changed and not edits):
            stages = _EDIT_STAGES
        elif edits:
            scene_ids = [e.scene_id for e in edits]
            stages = _EDIT_STAGES
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
            scene_instructions={e.scene_id: e.instruction for e in edits if e.instruction},
            scene_durations={
                e.scene_id: e.target_duration for e in edits if e.target_duration > 0
            },
            intent=intent if intent_changed else None,
            force_voice=plan.revoice,
        )

    # -- prompts -----------------------------------------------------------
    def _intent_prompt(self, message: str, current: VideoIntent, page_count: int) -> str:
        return (
            f"用户消息：{message}\n\n"
            f"文档页数：{page_count}\n\n"
            f"当前意图：{current.model_dump_json(indent=2)}"
        )

    def _edit_prompt(self, message: str, project: VideoProject) -> str:
        lines = [
            f"用户消息：{message}",
            "",
            f"当前意图：{project.intent.model_dump_json()}",
            "",
            "当前场景列表：",
        ]
        for scene in project.scenes:
            lines.append(
                f"- {scene.scene_id}（第 {scene.source_page} 页，{scene.duration:.0f} 秒）"
                f" {scene.title or scene.narration[:24]}"
            )
        return "\n".join(lines)

    @staticmethod
    def _sanitize_intent(intent: VideoIntent, page_count: int) -> VideoIntent:
        intent.duration = max(15, min(7200, int(intent.duration)))
        limit = page_count or 9999
        intent.emphasis_pages = sorted({p for p in intent.emphasis_pages if 1 <= p <= limit})
        intent.skip_pages = sorted({p for p in intent.skip_pages if 1 <= p <= limit})
        return intent


_EDIT_SYSTEM = """你在一个「文档转讲解视频」系统里，把用户的修改指令翻译成对现有工程的改动计划。

规则：
- 用户提到某一页或某个场景时，把改动放进 `scene_edits`，`instruction` 用一句话描述这一页要怎么改。
- `target_duration` 只在用户明确要求该场景的时长时填写（单位秒），否则填 0。
- 用户改的是全片时长、受众、风格这类全局设定时，更新 `intent`，`scene_edits` 留空。
- `rewrite_all` 只在用户要求整体重写讲稿时为 true。
- `revoice` 用于换音色、换语速；`redirect` 用于只改镜头（例如「所有关键数字都放大」）。
- `intent` 必须返回完整对象：用户没提到的字段沿用当前值。
- `summary` 一句话说明你将要做什么，会展示给用户。
"""


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
