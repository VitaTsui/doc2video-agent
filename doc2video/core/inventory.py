"""What is inside this build, and which of it works on this machine.

Two different questions get asked about a tool like this, and only the second
one is ever answered by a feature list. The first is "what does it do" — that
is the same on every machine and can be written down. The second is "does it
do it *here*" — and that changes with what is installed: a deck of old .ppt
needs LibreOffice, a voice needs its engine, a script needs a model, and a
video needs ffmpeg. When something silently does its lesser job, this is the
page that says why.

So each entry carries both: what the thing is for, and whether it is usable
right now. The pipeline order is the order the steps actually run in, because
that is the order in which a missing piece shows up in the result.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field

from .config import Settings, dependency_report, get_settings

# The binaries the render step itself needs. The dependency report covers
# more than this step's share of them.
RENDER_BINARIES = ("ffmpeg", "ffprobe", "node", "npx")


@dataclass
class Part:
    """One tool, as something that either works here or does not."""

    id: str
    name: str
    what: str
    available: bool
    # Why not, when it is not. Empty when it is.
    reason: str = ""


@dataclass
class Step:
    """One step of the pipeline: the skill that runs it, and what it can use."""

    id: str
    name: str
    skill: str
    what: str
    parts: list[Part] = field(default_factory=list)


def report(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    return {"steps": [_as_dict(step) for step in steps(settings)]}


def _as_dict(step: Step) -> dict:
    return {
        "id": step.id,
        "name": step.name,
        "skill": step.skill,
        "what": step.what,
        "parts": [
            {
                "id": part.id,
                "name": part.name,
                "what": part.what,
                "available": part.available,
                "reason": part.reason,
            }
            for part in step.parts
        ],
    }


def steps(settings: Settings | None = None) -> list[Step]:
    from ..skills import (
        DirectorSkill,
        DocumentSkill,
        MotionSkill,
        NarrationSkill,
        ReviewSkill,
        VoiceSkill,
    )
    from ..tools.llm import llm_status
    from ..tools.renderer import renderer_status
    from ..tools.tts import packs as voice_packs

    settings = settings or get_settings()
    binaries = dependency_report()
    renderers = renderer_status(settings)
    llm = llm_status(settings)
    model = Part(
        id="llm",
        # The provider, not the model id: which model answers is chosen in the
        # 「模型」 tab and changes there, and repeating it here only makes the
        # row longer than the sentence that says what it is for.
        name=llm["provider"] if llm["available"] else "模型",
        what="读懂页面、写讲稿、按一句话改稿。没有模型也能出片，讲稿要自己写。",
        available=bool(llm["available"]),
        reason="" if llm["available"] else "还没配模型，去「模型」那一栏加一条",
    )

    return [
        Step(
            id="parse",
            name="解析文档",
            skill="",
            what="把 PPT / PDF 拆成页面、标题、正文、图表和它们在页面上的位置。",
            parts=[
                Part("pptx", "python-pptx", "读 .pptx：元素、层级、图表数据都能直接读出来。", True),
                Part("pdf", "PyMuPDF", "读 .pdf：文字带坐标，页面按原样栅格化。", True),
                Part(
                    "soffice",
                    "LibreOffice",
                    "只用来把旧版 .ppt 转成 .pptx。没有它，旧 .ppt 打不开——另存为 .pptx 即可。",
                    shutil.which("soffice") is not None,
                    "" if shutil.which("soffice") else "没装，旧版 .ppt 请先另存为 .pptx",
                ),
            ],
        ),
        Step(
            id="understand",
            name="理解结构",
            skill=DocumentSkill.name,
            what=DocumentSkill.description + "：认页面类型、挑重点、排叙事顺序。",
            parts=[model],
        ),
        Step(
            id="narrate",
            name="生成讲稿",
            skill=NarrationSkill.name,
            what=NarrationSkill.description + "：按目标时长分配每页字数，写完再压回时长。",
            parts=[model],
        ),
        Step(
            id="voice",
            name="配音",
            skill=VoiceSkill.name,
            what=VoiceSkill.description + "：分句合成，给出句级时间戳，字幕跟着它走。",
            parts=[
                Part(
                    pack.id,
                    pack.name,
                    pack.note,
                    pack.installed,
                    "" if pack.installed else "还没装，去「语音」那一栏装",
                )
                for pack in voice_packs.catalogue(settings)
            ],
        ),
        Step(
            id="direct",
            name="镜头",
            skill=DirectorSkill.name,
            what=DirectorSkill.description + "：讲到哪就框到哪、推到哪，讲不清的地方不动。",
            parts=[],
        ),
        Step(
            id="motion",
            name="时间轴",
            skill=MotionSkill.name,
            what=MotionSkill.description + "：画面、字幕、镜头动作对齐到绝对时间。",
            parts=[],
        ),
        Step(
            id="render",
            name="渲染合成",
            skill="",
            what="逐页出片段，再拼成成片。没改的页沿用上次的片段，不重编码。",
            parts=[
                Part(
                    "remotion",
                    "Remotion",
                    "浏览器渲染：动画、图表重绘、字幕排版都在这条路上。",
                    renderers["remotion"]["available"],
                    renderers["remotion"]["reason"],
                ),
                Part(
                    "ffmpeg_renderer",
                    "纯 ffmpeg",
                    "兜底渲染：没有 Node 也能出片，动画能力有限。",
                    renderers["ffmpeg"]["available"],
                    renderers["ffmpeg"]["reason"],
                ),
                # Only the binaries this step actually reaches for. The
                # report also covers `soffice` and `say`, which belong to
                # opening a deck and to speaking it — listing them here would
                # put a missing LibreOffice under 「渲染合成」, where nobody
                # could act on it.
                *(
                    Part(
                        name,
                        name,
                        info["purpose"],
                        info["available"],
                        "" if info["available"] else "没找到，装一个或在设置里指定路径",
                    )
                    for name, info in binaries.items()
                    if name in RENDER_BINARIES
                ),
            ],
        ),
        Step(
            id="review",
            name="质检",
            skill=ReviewSkill.name,
            what=ReviewSkill.description + "：全是量出来的，不请模型给自己打分。",
            parts=[],
        ),
    ]
