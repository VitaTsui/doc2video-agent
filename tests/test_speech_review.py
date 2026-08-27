"""How the finished audio sounds, measured off the clips that already exist.

The review beside this one reads the project and the one beside that looks at
the frames. Neither can hear: a page can have a good script, correct timings
and a caption in the right place, and be delivered faster than anyone follows.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from doc2video.schemas import Scene, SceneAudio, Source, SourceType, VideoProject
from doc2video.skills.speech_review import TOO_FAST, TOO_SLOW, check_speech

LEAD, TAIL = 0.8, 0.6


def _clip(path: Path, spans: list[tuple[float, bool]], rate: int = 22050) -> float:
    frames = bytearray()
    for seconds, speaking in spans:
        for index in range(int(seconds * rate)):
            frames += struct.pack("<h", int(9000 * math.sin(index * 0.05)) if speaking else 0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return sum(seconds for seconds, _ in spans)


def _project(tmp_path: Path, narration: str, spans: list[tuple[float, bool]]) -> VideoProject:
    duration = _clip(tmp_path / "scene_01.wav", spans)
    project = VideoProject(
        project_id="proj_speech",
        source=Source(type=SourceType.PPTX, file="d.pptx", path="source/d.pptx", page_count=1),
    )
    project.scenes = [
        Scene(
            scene_id="scene_01",
            source_page=1,
            narration=narration,
            duration=duration,
            audio=SceneAudio(path="scene_01.wav", duration=duration),
        )
    ]
    return project


def _check(project: VideoProject, tmp_path: Path):
    return check_speech(project, lambda rel: tmp_path / rel, lead=LEAD, tail=TAIL)


def test_a_page_read_too_fast_is_reported(tmp_path: Path):
    """Found on a real deck: three scenes over 350 characters a minute.

    They only showed up once the lead and tail silence came off the duration —
    counted in, every scene reads slower than it is spoken and the check
    quietly passes everything.
    """
    # 60 characters over 8 seconds of speech: 450 a minute.
    project = _project(tmp_path, "字" * 60, [(LEAD, False), (8.0, True), (TAIL, False)])
    findings = _check(project, tmp_path)

    assert [f.kind for f in findings] == ["speech_rate"]
    assert "偏快" in findings[0].message


def test_a_page_read_too_slowly_is_reported(tmp_path: Path):
    project = _project(tmp_path, "字" * 10, [(LEAD, False), (8.0, True), (TAIL, False)])
    findings = _check(project, tmp_path)

    assert [f.kind for f in findings] == ["speech_rate"]
    assert "偏慢" in findings[0].message


def test_an_ordinary_pace_is_left_alone(tmp_path: Path):
    """The engines this ships with land near 290 characters a minute."""
    project = _project(tmp_path, "字" * 40, [(LEAD, False), (8.0, True), (TAIL, False)])
    assert _check(project, tmp_path) == []
    assert TOO_SLOW < 290 < TOO_FAST


def test_a_long_stretch_with_no_pause_is_reported(tmp_path: Path):
    """The guard is about the engine, not the script.

    `say` breaks at every mark and never runs past five seconds, so this does
    not fire on anything shipping today. It is here because the next provider
    is exactly the kind of thing that would regress it, and a check added
    after the regression is a check that arrived late.
    """
    project = _project(tmp_path, "字" * 60, [(LEAD, False), (14.0, True), (TAIL, False)])
    kinds = [f.kind for f in _check(project, tmp_path)]
    assert "monotone" in kinds


def test_a_scene_too_short_to_judge_is_not_judged(tmp_path: Path):
    project = _project(tmp_path, "两个字", [(LEAD, False), (0.4, True), (TAIL, False)])
    assert _check(project, tmp_path) == []


def test_a_uniform_page_is_allowed_a_uniform_script():
    """A contents page of five equal lines should be told in five equal sentences.

    The flatness check used to compare against a constant, so it asked a page
    with nothing to be varied about for variety — and the only way to obey is to
    pad, which is the AI tic the neighbouring check reports.
    """
    from doc2video.schemas import BBox, DocumentPage, ElementKind, PageType, SlideElement
    from doc2video.skills.review import _length_spread

    def page(texts: list[str]) -> DocumentPage:
        return DocumentPage(
            index=1,
            title="页",
            page_type=PageType.CONTENT,
            elements=[
                SlideElement(
                    id=f"e{i}",
                    kind=ElementKind.PARAGRAPH,
                    text=text,
                    bbox=BBox(x=0, y=i * 60, w=800, h=50),
                )
                for i, text in enumerate(texts)
            ],
        )

    flat_script = "第一，背景介绍。第二，痛点分析。第三，建设思路。第四，商业价值。"

    contents = page(["(一)背景及技术牵头方", "(二)核心市场痛点分析",
                     "(三)项目建设主旨思路", "(四)联合揭榜商业价值"])
    assert _length_spread(flat_script, contents) is None, "页面本身是齐的，讲稿齐着讲不该扣分"

    # The same script against a page of wildly different blocks: now it is the
    # script that flattened the page.
    uneven = page(["面向石化企业市场经营分析环节，分别建设供应链、外贸和招投标情报能力，"
                   "并通过统一数据底座实现相互关联与综合研判。",
                   "监测价格、供需与物流事件。", "回答：怎么买", "形成参与建议。"])
    assert _length_spread(flat_script, uneven) is not None, "页面长短悬殊，讲稿抹平了就该报"


def test_the_pages_own_words_are_not_the_writers_tic():
    """「不是A，是B」 on the slide is the slide talking.

    The script quoted 「未来不是慢一点，是直接失去生存席位」 and even attributed
    it — 「页面最后一句话是……」. Marking that down marks the script down for
    doing the one thing the grounding dimension asks of it.
    """
    from doc2video.skills.review import _quotes_page

    page_text = "企业不做组织 AI 赋能与转型，未来不是发展慢一点，是直接失去生存席位！"

    assert _quotes_page("不是慢一点，是", page_text), "少了一个词也还是页面的话"
    assert not _quotes_page("不是简单的堆砌，而是深度的融合", page_text), "页面没写的就是自己发挥"


def test_a_page_told_out_of_one_mould_is_reported():
    """Four cards, four sentences, one shape — the film is reading a table.

    The flatness check next door sees only half of this. 「贸易趋势分析，跟踪…」/
    「市场准入分析，汇总…」/「目标市场识别，看…」 runs 34, 35 and 34 characters and
    is caught for being flat; 「实时聚合，采集…」/「智能匹配，按…」/「竞争跟踪，监测
    …」/「偏好分析，看…」 runs 31, 33, 20, 20, varied enough to pass, and is the
    same mould. Both pages are real and both are what gets noticed.
    """
    from doc2video.skills.review import stamped_openings

    stamped = (
        "招投标情报，从全量招标信息里筛出更适配的项目机会。"
        "实时聚合，采集工程、装置、设备项目，清洗业主、金额和时间节点。"
        "智能匹配，按产品、地区、金额、资质匹配企业条件，形成优先跟进清单。"
        "竞争跟踪，监测对手参与的项目和中标结果。"
        "偏好分析，看招标方的采购周期和评标偏好。"
    )
    assert len(stamped_openings(stamped)) >= 3

    # The same four cards, entered a different way each time and let go short
    # at the end. Nothing is dropped; only the way in changes.
    spoken = (
        "先是实时聚合。工程、装置、设备的项目都采进来，业主、金额、时间节点洗一遍。"
        "洗完了做智能匹配，按产品、地区、金额和资质对企业条件，出一张优先跟进清单。"
        "另外两块轻一些：竞争跟踪盯对手投了哪些项目、中没中；"
        "偏好分析看招标方的采购周期和评标偏好。"
    )
    assert stamped_openings(spoken) == []


def test_what_is_not_a_mould():
    """The check has to leave alone every other reason a sentence has a comma."""
    from doc2video.skills.review import stamped_openings

    # One sentence that counts five things is not five stamped sentences.
    assert stamped_openings(
        "方案分五个部分。一是背景及技术牵头方，二是核心市场痛点分析，"
        "三是项目建设主旨思路，四是项目总体建设内容，五是联合揭榜商业价值。"
    ) == []

    # Clause openings long enough to be someone entering the block, not a label.
    assert stamped_openings(
        "供应链情报的第一步，是围绕重点原料建立常态化的监测清单。"
        "持续监测的对象有四类：价格与供需、库存与开工率、装置与进出口。"
        "在这上面智能识别市场变化，包括价格异常、供需缺口和供应商变化。"
    ) == []

    # Two in a row is a pair; the page may only hold two of the thing.
    assert stamped_openings(
        "实时聚合，采集工程、装置、设备项目。智能匹配，按产品和地区匹配企业条件。"
        "这两块合起来就是线索到清单的一条路。"
    ) == []
