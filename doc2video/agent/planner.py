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
    # Set when the script came from the caller rather than being written here.
    # Not the same question as "is `narrations` empty": a caller can submit an
    # empty script, and that is still a caller's script — every page falls back
    # to placeholder text. What it decides is only what the step is called, and
    # 「生成讲稿」 for a step that writes nothing is the wrong name.
    adopts_script: bool = False
    scene_narrations: dict[str, str] = Field(default_factory=dict)
    # Seconds a scene was asked to become. Informational: nothing here writes to
    # a length any more, but the caller needs the number to rewrite against it.
    # What the user asked for, per scene, in their own words. The script is
    # theirs to write, so an instruction can only become new text when a model
    # is configured; without one this is recorded as a degradation rather than
    # quietly producing a run that changes nothing.
    scene_instructions: dict[str, str] = Field(default_factory=dict)
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

    def write_plan(self, written: dict[int, str] | None = None) -> ExecutionPlan:
        """Write the script here, then make the whole video from it.

        The deck is already parsed and understood; what is missing is the
        words. They are written by the narration skill — page by page, against
        each page's character budget, under the writing prompt — rather than
        by whoever asked for this.

        That difference is measurable. On the same 30-page deck the skill wrote
        2135–2483 characters; a model asked for the whole deck in one answer
        wrote 1800 twice, and its pages came back with the AI tics the writing
        prompt exists to keep out — 4 of them on a 9-page deck that the skill's
        version had none of. One writing path, not two.
        """
        return ExecutionPlan(
            summary="逐页写讲稿并生成视频",
            stages=[
                Stage.NARRATE,
                Stage.VOICE,
                Stage.DIRECT,
                Stage.MOTION,
                Stage.RENDER,
                Stage.REVIEW,
            ],
            adopts_script=False,
            narrations=dict(written or {}),
        )

    def revoice_plan(self) -> ExecutionPlan:
        """Say the same words differently.

        Not a rewrite and not a redirect: the script and the shots are what
        someone already approved. The picture still has to be made again —
        captions are drawn into the frames and the camera moves are timed to
        sentence boundaries, so a different voice puts both at different
        seconds — but pages whose timing does not move keep their clips.
        """
        return ExecutionPlan(
            summary="换个声音重新配音",
            stages=[Stage.VOICE, Stage.MOTION, Stage.RENDER, Stage.REVIEW],
            force_voice=True,
        )

    def draft_plan(self, written: dict[int, str] | None = None) -> ExecutionPlan:
        """Write a first script for a deck that has already been read.

        Its own step, after the parse rather than inside it, for two reasons.
        The parse takes seconds and writing takes as long as the model takes,
        so folding them together would hold the deck back behind the words —
        and the deck is what makes the wait for the words legible. And it stops
        at the script: nothing is voiced or rendered from a draft nobody has
        looked at yet.
        """
        return ExecutionPlan(
            summary="逐页起草讲稿",
            stages=[Stage.NARRATE],
            adopts_script=False,
            narrations=dict(written or {}),
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
            adopts_script=True,
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

        if plan.rewrite_all or (intent_changed and not edits and not _only_sound(intent, project)):
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
            scene_instructions={e.scene_id: e.instruction for e in edits if e.instruction},
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




def _only_sound(intent: VideoIntent, project: VideoProject) -> bool:
    """Did this request change nothing but how the words are spoken?

    「换个声音」 changes the intent, and any intent change used to mean a full
    revision — so asking for a different voice rewrote the script, redirected
    the camera and re-rendered everything, when the words and the shots were
    exactly what the person wanted to keep. They asked to change the voice,
    not the video.

    A voice does change how long the script takes to say, and the script was
    budgeted against the old engine's pace. That is worth reporting rather
    than acting on: the review measures the finished length, and rewriting the
    words to protect a target duration is a different request from this one.
    """
    was, now = project.intent.model_dump(), intent.model_dump()
    changed = {key for key in now if was.get(key) != now[key]}
    return bool(changed) and changed <= {"voice", "speech_rate", "pronunciation", "instructions"}


# --------------------------------------------------------------------------
# Rule-based fallbacks — used when no LLM is configured, and as a safety net.
# --------------------------------------------------------------------------

# Numbers as people type them. 「7分钟」 was the only form these rules read,
# so 「做一个七分钟左右的讲解视频」 left the duration at its default 480 and the
# window then reported 「按这个要求算下来大约 480 秒」 — the request had been
# dropped and the confirmation claimed to have honoured it. Chinese numerals are
# not a rare phrasing; they are how the sentence gets typed when nobody is
# thinking about a form field.
_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_NUM = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十]{1,3})"


def _number(text: str) -> float | None:
    """Read 「7」/「7.5」/「七」/「十」/「十五」/「二十五」 as one number."""
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    if any(ch not in _CN_DIGIT and ch != "十" for ch in text):
        return None
    # 十 is a place, not a digit: 十五 is 15, 二十 is 20, 二十五 is 25.
    if "十" not in text:
        return sum(_CN_DIGIT[ch] for ch in text) if len(text) == 1 else None
    tens, _, ones = text.partition("十")
    high = _CN_DIGIT.get(tens, 1) if tens else 1
    low = _CN_DIGIT.get(ones, 0) if ones else 0
    if (tens and tens not in _CN_DIGIT) or (ones and ones not in _CN_DIGIT):
        return None
    return high * 10 + low


# 分 alone is too common to key on (「十分重要」「这部分」), so only the forms
# that can only mean a length: 分钟, 分半, 半分钟, 小时.
_MINUTES_RE = re.compile(rf"({_NUM})\s*分\s*(钟|半)")
_HALF_MINUTE = re.compile(r"半\s*分钟")
_HOURS_RE = re.compile(rf"({_NUM})\s*个?\s*(?:小时|钟头)(半)?")
_HALF_HOUR = re.compile(r"半\s*(?:小时|个小时|钟头)")
_SECONDS_RE = re.compile(rf"({_NUM})\s*秒")
# 「5 分 30 秒」 reads as 30 seconds to a pattern that only knows 分钟 and 秒 —
# and a 30-second video is further from what was asked for than not
# understanding at all.
_MIN_SEC = re.compile(rf"({_NUM})\s*分\s*({_NUM})\s*秒")


def minutes_in(message: str) -> float | None:
    """Minutes the message asks for, or None if it names no length."""
    if m := _HOURS_RE.search(message):
        hours = _number(m.group(1))
        if hours is not None:
            return hours * 60 + (30 if m.group(2) else 0)
    if _HALF_HOUR.search(message):
        return 30.0
    if m := _MINUTES_RE.search(message):
        minutes = _number(m.group(1))
        if minutes is not None:
            return minutes + (0.5 if m.group(2) == "半" else 0.0)
    if _HALF_MINUTE.search(message):
        return 0.5
    return None


def seconds_in(message: str) -> float | None:
    if m := _MIN_SEC.search(message):
        minutes, seconds = _number(m.group(1)), _number(m.group(2))
        if minutes is not None and seconds is not None:
            return minutes * 60 + seconds
    if m := _SECONDS_RE.search(message):
        return _number(m.group(1))
    return None


def stated_duration(message: str) -> float | None:
    """The whole-video length this message asks for, in seconds.

    Also the honest answer to 「did they say how long?」, which the window needs:
    a default is not something the user requested and should not be reported as
    though it were.
    """
    if _MIN_SEC.search(message):
        return seconds_in(message)
    if (minutes := minutes_in(message)) is not None:
        return minutes * 60
    return seconds_in(message)
_PAGE = re.compile(r"第\s*(\d+)\s*[页頁]")
_PAGE_RANGE = re.compile(r"第?\s*(\d+)\s*[~-—到至]\s*(\d+)\s*[页頁]")

# 「X 念 Y」/「X 读作 Y」: the one phrasing people reach for when a term is
# being said wrong. Deliberately narrow — a loose pattern here would rewrite
# words the narrator should have kept.
# The spoken form is letters and spaces — 「R A G」 is the whole point, so a
# pattern that stops at the first space captures 「R」 and nothing else.
_READ_AS = re.compile(
    r"([A-Za-z][A-Za-z0-9+]{1,15})\s*(?:念|读作|读成)\s*([A-Za-z0-9][A-Za-z0-9 ]{0,23})"
)

# 「中试基地是一个词」/「中试基地别断开」: what someone says after hearing a term
# cut in half. The synthesiser decides its own phrase boundaries and gets them
# wrong on words it does not know — measured on 「国家人工智能应用中试基地」,
# `say` stops 0.27 seconds after 中, which is long enough to hear. The fix is a
# space in front of the term, which is how the spoken form says 「a word starts
# here」 (see `TTSProvider.phrase_boundary`).
_ONE_WORD = re.compile(
    r"[「『\"'（(]?([^\s，。！？；：、,.!?;:「」『』\"'（）()]{2,12})[」』\"'）)]?"
    r"\s*(?:是一个词|是个词|是一个词组|别断开|不要断开|不能断开|连着念|连读)"
)

# Words a sentence starts with before it gets to the term.
_LEAD_INS = ("另外", "还有", "而且", "顺便", "对了", "记得", "请把", "请", "帮我", "把", "然后")

_STYLE_HINTS = {
    "专业": "professional",
    "科技": "tech",
    "活泼": "lively",
    "轻松": "casual",
    "正式": "formal",
}

# How the narration should sound, said in words the writer can act on. The
# field existed, the prompt read it, and nothing ever wrote it — every deck
# ever made came out 「清晰、稳重」 no matter what was asked for. Keyed by the
# words people actually type.
_TONE_HINTS = {
    "亲切": "亲切、像在跟人聊",
    "热情": "热情、有推进感",
    "沉稳": "沉稳、不急不缓",
    "稳重": "清晰、稳重",
    "严肃": "严肃、克制",
    "干脆": "干脆、句子短",
    "冷静": "冷静、只讲事实",
    "幽默": "轻快、偶尔带一点玩笑",
    "年轻": "年轻、口语化",
}

# Speaking speed, as people ask for it. Multipliers on the machine's natural
# pace, kept mild: past about a quarter either way the synthesiser stops
# sounding like a person reading and starts sounding like a tape.
_RATE_HINTS = (
    (("再慢一点", "慢很多", "太快了"), 0.85),
    (("慢一点", "慢些", "放慢"), 0.9),
    (("快一点", "快些", "加快", "太慢了"), 1.12),
    (("再快一点", "快很多"), 1.25),
)

# When only a style was named, it implies how it should sound. Stated so that
# 「活泼一点」 changes the delivery too, rather than only a label nobody reads.
_STYLE_TONE = {
    "professional": "清晰、稳重",
    "tech": "干脆、重逻辑",
    "lively": "轻快、有起伏",
    "casual": "放松、像同事之间讲事",
    "formal": "持重、书面",
}


def parse_intent_rules(message: str, current: VideoIntent) -> VideoIntent:
    """Regex intent parsing — covers the phrasings users actually type."""
    intent = current.model_copy(deep=True)

    if (asked := stated_duration(message)) is not None:
        intent.duration = int(asked)
        intent.duration_stated = True

    for keyword, style in _STYLE_HINTS.items():
        if keyword in message:
            intent.style = style
            intent.tone = _STYLE_TONE.get(style, intent.tone)
            break

    # An explicit tone wins over the one the style implies: 「专业一点，但亲切」
    # names both, and the second one is the correction.
    for keyword, tone in _TONE_HINTS.items():
        if keyword in message:
            intent.tone = tone
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

    # Longest phrase first: 「再慢一点」 contains 「慢一点」, and the shorter
    # match would quietly take a smaller step than the one that was asked for.
    for words, rate in sorted(_RATE_HINTS, key=lambda item: -max(len(w) for w in item[0])):
        if any(word in message for word in words):
            intent.speech_rate = rate
            break

    if voice := _voice_from(message):
        intent.voice = voice

    intent.pronunciation.update(_pronunciations_in(message))

    intent.instructions = message.strip()[:500]
    return intent


# Voices people ask for by character rather than by name. The value is the
# voice that character maps to, whichever engine owns it — 「播音腔」 is a
# request about how it should sound, not about which package is installed.
_VOICE_CHARACTER = {
    "播音": "zh-CN-YunyangNeural",
    "新闻": "zh-CN-YunyangNeural",
    "主播": "zh-CN-YunyangNeural",
    "解说": "zh-CN-YunjianNeural",
}


def _voice_from(message: str) -> str:
    """A voice named in the message, if this machine has one to match.

    Asked for by kind (「换个女声」) rather than by name, almost always — so the
    words map onto whatever the machine actually installed, and a machine with
    no Chinese voices gets nothing rather than a name that will fail at
    synthesis time.
    """
    from ..tools.tts import gender_of, voices_available
    from ..tools.tts.edge import EdgeProvider

    # A character asked for by name, if the engine that has it is installed.
    for word, voice in _VOICE_CHARACTER.items():
        if word in message and voice in EdgeProvider().voices():
            return voice

    installed = voices_available()
    if not installed:
        return ""
    lowered = message.lower()
    for name in installed:
        # By the speaker's name rather than the whole listing entry: nobody
        # types 「用 Flo (中文（中国大陆）) 讲」.
        if name.split("(")[0].strip().lower() in lowered:
            return name
    wanted = None
    if any(word in message for word in ("女声", "女生", "女的")):
        wanted = "female"
    elif any(word in message for word in ("男声", "男生", "男的")):
        wanted = "male"
    if wanted is None:
        return ""
    return next((name for name in installed if gender_of(name) == wanted), "")


def _pronunciations_in(message: str) -> dict[str, str]:
    """The deck's own vocabulary, said the way this deck says it.

    Two phrasings, one dictionary:

    * 「RAG 念 R A G」 — the engine reads an initialism as a word.
    * 「中试基地是一个词」 — the engine breaks the term in half. The spoken form
      is the term with a boundary in front of it; the caption keeps the term.
    """
    learned = {term.strip(): spoken.strip() for term, spoken in _READ_AS.findall(message)}
    for match in _ONE_WORD.findall(message):
        # 「另外中试基地是一个词」 — the sentence's own connective is not part of
        # the term, and a boundary in front of 「另外」 would be a pause in a
        # place nobody meant.
        term = match.strip()
        for lead in _LEAD_INS:
            if term.startswith(lead) and len(term) > len(lead) + 1:
                term = term[len(lead) :]
        if term:
            learned[term] = f" {term}"
    return learned


def parse_edit_rules(message: str, project: VideoProject) -> EditPlan:
    """Regex edit parsing. Deliberately conservative: when a message cannot be
    mapped confidently, do the cheapest safe thing rather than rewriting everything."""
    intent = project.intent.model_copy(deep=True)
    scene_edits: list[SceneEdit] = []

    pages = [int(p) for p in _PAGE.findall(message)]
    mentions_page = bool(pages)

    target_seconds = 0.0
    minutes = minutes_in(message)
    if (secs := seconds_in(message)) is not None:
        target_seconds = secs
    elif minutes is not None:
        if mentions_page:
            target_seconds = minutes * 60
        else:
            intent.duration = int(minutes * 60)
            intent.duration_stated = True

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
    elif target_seconds and minutes is None:
        intent.duration = int(target_seconds)
        intent.duration_stated = True

    # A term said wrong is noticed *after* hearing it, which means it is said
    # in the second message and every one after — and this path never read the
    # dictionary, so 「RAG 念 R A G」 worked only if you predicted the problem
    # before the first render.
    learned = _pronunciations_in(message)
    intent.pronunciation.update(learned)

    revoice = bool(learned) or any(
        k in message for k in ("音色", "声音", "配音", "语速", "换个声音")
    )
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
