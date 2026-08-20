"""Everything this build is made of, as a list of plugins.

Two different questions get asked about a tool like this, and only the second
is ever answered by a feature list. The first is "what does it do" — the same
on every machine, and writable down. The second is "does it do it *here*",
which changes with what is installed: a deck of old .ppt needs LibreOffice, a
voice needs its engine, a script needs a model, a video needs ffmpeg. When
something silently does its lesser job, this is the page that says why.

Listed flat rather than grouped by pipeline step. The pipeline was the honest
first cut — it is the order in which a missing piece shows up in the result —
but it buries the thing people come here for: *what is installed*. A step with
one tool under it reads as a step; twenty tools spread across eight steps read
as nothing. So each plugin says which step it belongs to and the list stays a
list, searchable by any of it.

Detail rather than a name and a dot: a skill's prompt is shown in full because
a paraphrase is the thing you cannot check the output against, and the numbers
that decide what comes out are shown as the values they currently hold — with
their measured defaults beside them, since anyone may have changed one.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field

from .config import Settings, dependency_report, get_settings

# The binaries the render step reaches for. The dependency report covers more
# than that — `soffice` belongs to opening a deck and `say` to speaking one.
RENDER_BINARIES = ("ffmpeg", "ffprobe", "node", "npx")


@dataclass
class Rule:
    """One number that decides what comes out, and what it decides.

    Read from `core.tuning` rather than written down again here: a number
    copied into a description is a number that will be wrong later, and the
    whole point of showing these is that they are the real ones — including
    when someone has changed them.
    """

    name: str
    value: str
    what: str
    # The knob behind it, when it is one that can be changed. Empty for the
    # rules that are not a number (「不框的页」 is three page types).
    id: str = ""
    number: float = 0.0
    default: float = 0.0
    low: float = 0.0
    high: float = 0.0
    unit: str = ""
    integer: bool = False


@dataclass
class Plugin:
    """One part of this build, as something to look up."""

    id: str
    name: str
    # skill | parser | voice | renderer | model | binary
    kind: str
    # Which step of the pipeline it belongs to. An attribute, not a heading.
    stage: str
    what: str
    available: bool = True
    # Why not, when it is not. Empty when it is.
    reason: str = ""
    # What this plugin tells the model, verbatim. Empty when it asks none.
    prompt: str = ""
    rules: list[Rule] = field(default_factory=list)
    # Anything else worth reading: a path, a size, where it came from.
    detail: dict[str, str] = field(default_factory=dict)


KIND_NAME = {
    "skill": "技能",
    "parser": "解析器",
    "voice": "语音引擎",
    "renderer": "渲染器",
    "model": "模型",
    "binary": "可执行文件",
}


def report(settings: Settings | None = None) -> dict:
    found = plugins(settings)
    return {
        "kinds": KIND_NAME,
        "plugins": [
            {
                "id": item.id,
                "name": item.name,
                "kind": item.kind,
                "kind_name": KIND_NAME.get(item.kind, item.kind),
                "stage": item.stage,
                "what": item.what,
                "available": item.available,
                "reason": item.reason,
                "prompt": item.prompt,
                "detail": item.detail,
                "rules": [
                    {
                        "name": rule.name,
                        "value": rule.value,
                        "what": rule.what,
                        "id": rule.id,
                        "number": rule.number,
                        "default": rule.default,
                        "low": rule.low,
                        "high": rule.high,
                        "unit": rule.unit,
                        "integer": rule.integer,
                    }
                    for rule in item.rules
                ],
            }
            for item in found
        ],
    }
def _shown(value: float, unit: str, integer: bool) -> str:
    """A number the way this particular number is read."""
    if integer:
        text = f"{value:.0f}"
    elif unit == "×":
        text = f"{value:g}"
    elif unit == "字/分" or unit == "秒" and value >= 3:
        text = f"{value:.0f}"
    else:
        text = f"{value:.2f}"
    return f"{text} {unit}".strip() if unit not in ("", "×") else f"{text}{unit}"


def _rules(prefix: str, settings: Settings | None = None) -> list[Rule]:
    """The knobs of one step, as rules — values as they are set right now."""
    from . import tuning

    return [
        Rule(
            name=knob["name"],
            value=_shown(knob["value"], knob["unit"], knob["integer"]),
            what=knob["what"],
            id=knob["id"],
            number=knob["value"],
            default=knob["default"],
            low=knob["low"],
            high=knob["high"],
            unit=knob["unit"],
            integer=knob["integer"],
        )
        for knob in tuning.report(settings)
        if knob["id"].startswith(prefix)
    ]


def _prompt(load, name: str) -> str:
    """A prompt, or nothing. An unreadable file is not worth failing over."""
    try:
        return load(name)
    except OSError:
        return ""


def plugins(settings: Settings | None = None) -> list[Plugin]:
    """Every part of this build, in the order the pipeline reaches for them."""
    from ..agent import loop as agent_loop
    from ..skills import (
        DirectorSkill,
        DocumentSkill,
        MotionSkill,
        NarrationSkill,
        ReviewSkill,
        VoiceSkill,
    )
    from ..skills.base import load_prompt
    from ..tools.llm import llm_status
    from ..tools.renderer import renderer_status
    from ..tools.tts import packs as voice_packs

    settings = settings or get_settings()
    llm = llm_status(settings)
    renderers = renderer_status(settings)
    binaries = dependency_report()
    soffice = shutil.which("soffice")

    model = Plugin(
        id=f"llm:{llm['configured']}",
        name=llm["provider"] if llm["available"] else "模型",
        kind="model",
        stage="理解结构 · 生成讲稿 · 对话",
        what="读懂页面、写讲稿、按一句话改稿。没有模型也能出片，讲稿要自己写。",
        available=bool(llm["available"]),
        reason="" if llm["available"] else "还没配模型，去「模型」那一栏加一条",
        detail={"模型": str(llm["model"]), "接入方式": str(llm["configured"])},
    )

    listed: list[Plugin] = [
        Plugin(
            id="parser:python-pptx",
            name="python-pptx",
            kind="parser",
            stage="解析文档",
            what="读 .pptx：元素、层级、图表数据都能直接读出来，镜头能对准到具体形状。",
            detail={"格式": ".pptx"},
        ),
        Plugin(
            id="parser:pymupdf",
            name="PyMuPDF",
            kind="parser",
            stage="解析文档",
            what="读 .pdf：文字带坐标，页面按原样栅格化；版面靠还原，阅读顺序靠推断。",
            detail={"格式": ".pdf"},
        ),
        Plugin(
            id="parser:soffice",
            name="LibreOffice",
            kind="parser",
            stage="解析文档",
            what="只用来把旧版 .ppt 转成 .pptx。没有它，旧 .ppt 打不开——另存为 .pptx 即可。",
            available=soffice is not None,
            reason="" if soffice else "没装，旧版 .ppt 请先另存为 .pptx",
            detail={"路径": soffice or "未找到", "格式": ".ppt"},
        ),
        Plugin(
            id=DocumentSkill.name,
            name="理解结构",
            kind="skill",
            stage="理解结构",
            what=DocumentSkill.description + "：认页面类型、挑重点、排叙事顺序。",
            available=model.available,
            reason=model.reason,
            prompt=_prompt(load_prompt, "document_understanding"),
        ),
        Plugin(
            id=NarrationSkill.name,
            name="生成讲稿",
            kind="skill",
            stage="生成讲稿",
            what=NarrationSkill.description + "：按目标时长分配每页字数，写完再压回时长。",
            available=model.available,
            reason=model.reason,
            prompt=_prompt(load_prompt, "narration"),
        ),
        Plugin(
            id=VoiceSkill.name,
            name="配音",
            kind="skill",
            stage="配音",
            what=VoiceSkill.description + "：分句合成，给出句级时间戳，字幕跟着它走。",
            rules=_rules("voice.", settings),
        ),
        Plugin(
            id=DirectorSkill.name,
            name="镜头",
            kind="skill",
            stage="镜头",
            what=DirectorSkill.description + "：讲到哪就框到哪、推到哪，讲不清的地方不动。",
            rules=[
                *_rules("shot.", settings),
                Rule("不框的页", "封面 / 目录 / 章节页", "这几种页面上没有要指的东西"),
            ],
        ),
        Plugin(
            id=MotionSkill.name,
            name="时间轴",
            kind="skill",
            stage="时间轴",
            what=MotionSkill.description + "：画面、字幕、镜头动作对齐到绝对时间。",
        ),
        Plugin(
            id=ReviewSkill.name,
            name="质检",
            kind="skill",
            stage="质检",
            what=ReviewSkill.description + "：全是量出来的，不请模型给自己打分。",
            rules=_rules("review.", settings),
        ),
        Plugin(
            id="agent:loop",
            name="对话决策",
            kind="skill",
            stage="对话",
            what="出片之后你说一句「第 3 页太长了」，是它决定接下来做什么：重写哪几页、"
            "换个声音、还是先问你一句。它只能用这里的其它插件，没有别的权限。",
            available=model.available,
            reason=model.reason,
            prompt=agent_loop._SYSTEM,  # noqa: SLF001 - the text is the point
        ),
        model,
    ]

    listed += [
        Plugin(
            id=f"tts:{pack.id}",
            name=pack.name,
            kind="voice",
            stage="配音",
            what=pack.note + ("　" + pack.how if pack.how else ""),
            available=pack.installed,
            reason="" if pack.installed else "还没装，去「语音」那一栏装",
            detail={
                "音色": "、".join(voice["name"] for voice in pack.voices) or "（还没有）",
                **({"要联网": "是"} if pack.online else {}),
                **({"文件夹": pack.folder} if pack.folder else {}),
            },
        )
        for pack in voice_packs.catalogue(settings)
    ]

    listed += [
        Plugin(
            id="renderer:remotion",
            name="Remotion",
            kind="renderer",
            stage="渲染合成",
            what="浏览器渲染：动画、图表重绘、字幕排版都在这条路上。",
            available=renderers["remotion"]["available"],
            reason=renderers["remotion"]["reason"],
        ),
        Plugin(
            id="renderer:ffmpeg",
            name="纯 ffmpeg",
            kind="renderer",
            stage="渲染合成",
            what="兜底渲染：没有 Node 也能出片，动画能力有限。",
            available=renderers["ffmpeg"]["available"],
            reason=renderers["ffmpeg"]["reason"],
        ),
    ]

    listed += [
        Plugin(
            id=f"bin:{name}",
            name=name,
            kind="binary",
            stage="渲染合成",
            what=info["purpose"],
            available=info["available"],
            reason="" if info["available"] else "没找到，装一个或在设置里指定路径",
            detail={"路径": info.get("path") or "未找到", "来源": info.get("source", "")},
        )
        for name, info in binaries.items()
        if name in RENDER_BINARIES
    ]
    return listed
