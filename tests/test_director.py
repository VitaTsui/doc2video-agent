"""Director rules — the guardrails that keep camera work readable."""

from __future__ import annotations

from doc2video.core.config import Settings
from doc2video.schemas import (
    ActionType,
    BBox,
    DocumentModel,
    DocumentPage,
    ElementKind,
    NarrationSegment,
    Scene,
    SlideElement,
    Source,
    SourceType,
    VideoProject,
)
from doc2video.skills.base import SkillContext
from doc2video.skills.director import DirectorSkill
from doc2video.storage import ProjectStore


def _page(elements: list[SlideElement]) -> DocumentPage:
    return DocumentPage(index=1, title="测试页", elements=elements, width=1000, height=1000)


def _element(element_id: str, box: BBox, kind: ElementKind = ElementKind.NUMBER) -> SlideElement:
    return SlideElement(id=element_id, kind=kind, text=element_id, bbox=box, importance=0.9)


def _run(page: DocumentPage, scene: Scene, settings: Settings, store: ProjectStore) -> Scene:
    project = VideoProject(
        project_id="proj_director",
        source=Source(type=SourceType.PPTX, file="d.pptx", path="source/d.pptx"),
        document=DocumentModel(pages=[page]),
        scenes=[scene],
    )
    ctx = SkillContext.build(project, store=store, settings=settings)
    DirectorSkill(ctx).run()
    return project.scenes[0]


def _scene(segments: list[NarrationSegment], duration: float = 12.0) -> Scene:
    return Scene(
        scene_id="scene_01",
        source_page=1,
        narration="".join(s.text for s in segments),
        segments=segments,
        duration=duration,
    )


def test_every_action_fits_inside_the_scene(settings: Settings, store: ProjectStore):
    page = _page([_element("e_small", BBox(x=100, y=100, w=200, h=150))])
    scene = _scene(
        [
            NarrationSegment(id="s1", text="讲 e_small 这个数字", element_refs=["e_small"],
                             emphasis=True, start=0.0, end=6.0),
            NarrationSegment(id="s2", text="收尾", start=6.0, end=12.0),
        ]
    )
    result = _run(page, scene, settings, store)

    assert result.actions
    for action in result.actions:
        assert action.at >= 0
        assert action.at + action.duration <= result.duration + 0.01


def test_large_target_is_highlighted_rather_than_zoomed(settings: Settings, store: ProjectStore):
    # Covers 64% of the page — zooming it would magnify everything, singling out nothing.
    page = _page([_element("e_huge", BBox(x=100, y=100, w=800, h=800))])
    scene = _scene(
        [
            NarrationSegment(id="s1", text="讲 e_huge", element_refs=["e_huge"],
                             emphasis=True, start=0.0, end=6.0)
        ]
    )
    result = _run(page, scene, settings, store)

    targeted = [a for a in result.actions if a.target == "e_huge"]
    assert targeted
    assert all(a.type is ActionType.HIGHLIGHT for a in targeted)


def test_nothing_is_zoomed_any_more(settings: Settings, store: ProjectStore):
    """「镜头zoom就不需要了」 — the camera outlines, it never pushes in.

    A push-in crops the page away and magnifies every timing or coordinate
    error with it. Even the case that used to zoom — a small emphasised
    number — gets the outline every other mention gets."""
    page = _page([_element("e_tiny", BBox(x=400, y=400, w=150, h=100))])
    scene = _scene(
        [
            NarrationSegment(id="s1", text="讲 e_tiny", element_refs=["e_tiny"],
                             emphasis=True, start=0.0, end=6.0)
        ]
    )
    result = _run(page, scene, settings, store)
    targeted = [a for a in result.actions if a.target == "e_tiny"]
    assert targeted
    assert all(a.type is not ActionType.ZOOM for a in result.actions)


def test_brief_mention_of_a_small_target_uses_a_pointer(
    settings: Settings, store: ProjectStore
):
    # 1% of the page, mentioned in passing — a highlight box would be larger
    # than the thing it is pointing at.
    page = _page([_element("e_speck", BBox(x=400, y=400, w=100, h=100), ElementKind.BULLET)])
    scene = _scene(
        [
            NarrationSegment(id="s1", text="顺带提一下 e_speck", element_refs=["e_speck"],
                             start=0.0, end=2.0),
            NarrationSegment(id="s2", text="继续讲别的", start=2.0, end=12.0),
        ]
    )
    result = _run(page, scene, settings, store)

    targeted = [a for a in result.actions if a.target == "e_speck"]
    assert targeted
    assert all(a.type is ActionType.POINTER for a in targeted)


def test_a_dwelt_on_target_is_highlighted_not_pointed_at(
    settings: Settings, store: ProjectStore
):
    """The same small element, talked about at length, deserves a lasting mark."""
    page = _page([_element("e_speck", BBox(x=400, y=400, w=100, h=100), ElementKind.BULLET)])
    scene = _scene(
        [
            NarrationSegment(id="s1", text="展开讲 e_speck", element_refs=["e_speck"],
                             start=0.0, end=8.0),
        ]
    )
    result = _run(page, scene, settings, store)

    targeted = [a for a in result.actions if a.target == "e_speck"]
    assert targeted
    assert all(a.type is not ActionType.POINTER for a in targeted)


def test_repeated_reference_produces_one_move(settings: Settings, store: ProjectStore):
    page = _page([_element("e_one", BBox(x=100, y=100, w=200, h=150))])
    segments = [
        NarrationSegment(id=f"s{i}", text=f"再讲一次 e_one（{i}）", element_refs=["e_one"],
                         emphasis=True, start=float(i * 3), end=float(i * 3 + 3))
        for i in range(4)
    ]
    result = _run(page, _scene(segments), settings, store)

    moves = [a for a in result.actions if a.target == "e_one"]
    assert len(moves) == 1


def test_scene_always_opens_with_a_transition(settings: Settings, store: ProjectStore):
    page = _page([_element("e", BBox(x=10, y=10, w=100, h=100))])
    result = _run(page, _scene([NarrationSegment(id="s1", text="随便讲", start=0, end=5)]),
                  settings, store)

    assert result.actions[0].type is ActionType.TRANSITION
    assert result.actions[0].at == 0.0


def test_actions_never_target_unknown_elements(settings: Settings, store: ProjectStore):
    page = _page([_element("e_real", BBox(x=10, y=10, w=100, h=100))])
    scene = _scene(
        [
            NarrationSegment(id="s1", text="讲一个不存在的东西",
                             element_refs=["e_ghost"], start=0.0, end=5.0)
        ]
    )
    result = _run(page, scene, settings, store)

    known = {"e_real"}
    assert all(a.target in known for a in result.actions if a.target)


def test_a_signpost_page_gets_no_box_drawn_on_it():
    """A cover and a section divider — nothing on them to point at.

    Both say the same thing: here is where we are. A deck went out with a
    highlight around the numeral on a divider, which read as the camera
    pointing at furniture. The page change is the whole gesture.

    A table of contents is not one of them, though it was, and that cost it
    thirteen shots on a thirty-page deck: its items are the one thing on the
    page worth pointing at, and the narration walks them one by one. What the
    rule was protecting against there was the heading 「CONTENTS」, which
    `_worth_pointing_at` refuses on its own.
    """
    from doc2video.schemas import PageType
    from doc2video.skills.director import SIGNPOST_PAGES

    assert {PageType.COVER, PageType.SECTION} == SIGNPOST_PAGES
    assert PageType.AGENDA not in SIGNPOST_PAGES


def test_page_furniture_is_never_a_target():
    """A bullet, a page number, a stock heading: text with nothing in it."""
    from doc2video.schemas import BBox, ElementKind, SlideElement
    from doc2video.skills.director import _worth_pointing_at

    def element(text: str, kind: ElementKind = ElementKind.PARAGRAPH) -> SlideElement:
        return SlideElement(
            id="e1", kind=kind, text=text, bbox=BBox(x=0, y=0, w=100, h=30), label=text
        )

    assert not _worth_pointing_at(element("1"))
    assert not _worth_pointing_at(element("·"))
    assert not _worth_pointing_at(element("CONTENTS"))
    assert not _worth_pointing_at(element("目录"))
    assert _worth_pointing_at(element("16+ 数据来源"))
    # A picture is the point of the page even with no text on it at all.
    assert _worth_pointing_at(element("", ElementKind.CHART))


def test_the_page_heading_is_never_the_thing_pointed_at():
    """Boxing the page's own title says nothing.

    The viewer is already on the page and its title is what the whole page is
    about, so a box drawn round it reads as the camera having nowhere better
    to go. Found in a finished video: three shots aimed at the page title, two
    of them at text identical to it.

    The fallback branch had always skipped titles; the branch that takes the
    model's own `element_refs` did not, so a heading the model bound went
    straight through.
    """
    from doc2video.schemas import BBox, DocumentPage, ElementKind, SlideElement
    from doc2video.skills.director import _is_banner

    def element(text: str, kind: ElementKind = ElementKind.SUBTITLE) -> SlideElement:
        return SlideElement(
            id="e1", kind=kind, text=text, bbox=BBox(x=0, y=0, w=400, h=60), label=text
        )

    page = DocumentPage(
        index=1, title="供应链情报：持续监测价格与供需变化", width=1920, height=1080
    )

    assert _is_banner(element("任何文字", ElementKind.TITLE), page)
    # Some decks mark the heading as something other than a title; the text is
    # what gives it away.
    assert _is_banner(element("供应链情报：持续监测价格与供需变化"), page)
    assert not _is_banner(element("十六个以上数据来源"), page)


def test_emphasis_and_figures_get_the_outline_not_a_zoom():
    """Every kind of mention resolves to a box or a pointer — never a push-in."""
    from doc2video.schemas import BBox, DocumentPage, ElementKind, NarrationSegment, SlideElement
    from doc2video.skills.director import DirectorSkill

    def element(text: str, kind: ElementKind = ElementKind.PARAGRAPH) -> SlideElement:
        return SlideElement(
            id="e1", kind=kind, text=text, bbox=BBox(x=0, y=0, w=300, h=80), label="e1"
        )

    page = DocumentPage(index=1, width=1920, height=1080)
    stressed = NarrationSegment(id="s1", text="这一句是重点。", emphasis=True, start=0.0, end=3.0)

    assert DirectorSkill._pick_action(stressed, element("十六个数据来源"), page) is (
        ActionType.HIGHLIGHT
    )
    assert DirectorSkill._pick_action(stressed, element("", ElementKind.CHART), page) is (
        ActionType.HIGHLIGHT
    )


# The slide this came from, at its own coordinates: two picture cards side by
# side, each with two lines of caption under it, a heading across the top and
# artwork bleeding out of two corners.
def _cards_page() -> DocumentPage:
    def at(element_id, x, y, w, h, kind=ElementKind.PARAGRAPH, text="x"):
        return SlideElement(
            id=element_id, kind=kind, text=text, bbox=BBox(x=x, y=y, w=w, h=h), importance=0.8
        )

    return DocumentPage(
        index=6,
        title="AI技术优势",
        width=1920,
        height=1080,
        elements=[
            at("backdrop_bottom", 0, 837, 623, 243, ElementKind.IMAGE, ""),
            at("backdrop_corner", 1473, 0, 447, 176, ElementKind.IMAGE, ""),
            at("heading", 204, 57, 1176, 80, ElementKind.TITLE, "AI技术优势"),
            at("left_card", 114, 387, 855, 493, ElementKind.IMAGE, ""),
            at("right_card", 996, 387, 755, 493, ElementKind.IMAGE, ""),
            at("left_caption_1", 395, 904, 220, 26, text="浙江大学作为申报单位的"),
            at("left_caption_2", 405, 928, 200, 26, text="国家重点研发计划项目"),
            at("right_caption_1", 1264, 904, 220, 26, text="浙江大学作为链主单位的"),
            at("right_caption_2", 1274, 928, 200, 26, text="高质量数据集建设项目"),
        ],
    )


def test_a_caption_is_framed_with_what_it_captions():
    """「国家重点研发计划项目」 is twenty characters at the bottom of a slide.

    A box around them points at the label instead of at the picture card they
    belong to — which is the thing the sentence is actually about.
    """
    from doc2video.skills.director import focus_box

    page = _cards_page()
    box = focus_box(page.element("left_caption_2"), page)

    # The card, plus both lines of its caption.
    assert (round(box.x), round(box.y)) == (114, 387)
    assert (round(box.w), round(box.h)) == (855, 567)


def test_the_identical_card_beside_it_is_left_out():
    """Growing sideways would turn 「这一个」 into 「这两个」, a different sentence."""
    from doc2video.skills.director import focus_box

    page = _cards_page()
    left = focus_box(page.element("left_caption_2"), page)
    right = focus_box(page.element("right_caption_2"), page)

    assert left.x + left.w <= right.x
    assert (round(right.x), round(right.w)) == (996, 755)


def test_the_artwork_bleeding_off_the_corner_is_not_a_group():
    """Decoration contains half the slide without being what any of it belongs to.

    The first version of this grouped the caption with the graphic behind it
    and framed the bottom-left corner of the page.
    """
    from doc2video.skills.director import focus_box

    page = _cards_page()
    box = focus_box(page.element("left_caption_2"), page)

    assert box.y < 837, "框到了那张衬底图上"


def test_a_thing_that_is_already_the_page_is_not_a_group():
    """On a dense diagram the only thing that spans a label is the whole diagram.

    Framing 44% of a slide points at nothing, so the label keeps its own box
    and the rest of the director decides — correctly — that it is not a target.
    """
    from doc2video.skills.director import focus_box

    page = DocumentPage(
        index=1,
        title="示意图",
        width=1920,
        height=1080,
        elements=[
            SlideElement(
                id="hub",
                kind=ElementKind.IMAGE,
                text="",
                bbox=BBox(x=83, y=302, w=1744, h=521),
                importance=0.5,
            ),
            SlideElement(
                id="label",
                kind=ElementKind.PARAGRAPH,
                text="安全可审计",
                bbox=BBox(x=431, y=565, w=140, h=37),
                importance=0.9,
            ),
        ],
    )

    assert focus_box(page.element("label"), page) == page.element("label").bbox


def test_a_long_page_can_carry_more_than_four_shots():
    """The budget is the page's own length, not a number picked once.

    A flat cap of four was measured against nothing: on a page that is on
    screen for twenty-seven seconds and names five different boxes, the fifth
    sentence pointed at nothing — the voice says 「再看这一块」 and the picture
    does not move.
    """
    from doc2video.schemas import Scene
    from doc2video.skills.director import (
        MAX_ACTIONS_PER_SCENE,
        MIN_ACTIONS_PER_SCENE,
        _action_budget,
    )

    # What limits this is how long a box has to stay up to be read, not a
    # rate: a 19-second contents page names five sections, and 「one every six
    # seconds」 gave it three boxes — two sections were read out with the camera
    # sitting on somebody else.
    assert _action_budget(Scene(scene_id="s", duration=2.0)) == MIN_ACTIONS_PER_SCENE
    assert _action_budget(Scene(scene_id="s", duration=19.0)) >= 5
    assert _action_budget(Scene(scene_id="s", duration=600.0)) == MAX_ACTIONS_PER_SCENE


def test_a_sentence_that_names_two_things_gets_two_boxes(
    settings: Settings, store: ProjectStore
):
    """「第一部分是背景及技术牵头方，第二部分是核心市场痛点分析。」

    One box per sentence sat on whichever half matched better and stayed there
    through the other half — the narration said 第一部分 while the box was on
    (二). The clause is what walks the page, so the clause is what the camera
    follows, timed by where it falls in the sentence.
    """
    page = _page(
        [
            SlideElement(
                id="e1",
                kind=ElementKind.PARAGRAPH,
                text="(一)背景及技术牵头方",
                bbox=BBox(x=600, y=200, w=300, h=60),
                importance=0.8,
            ),
            SlideElement(
                id="e2",
                kind=ElementKind.PARAGRAPH,
                text="(二)核心市场痛点分析",
                bbox=BBox(x=600, y=400, w=300, h=60),
                importance=0.8,
            ),
        ]
    )
    segment = NarrationSegment(
        id="scene_01_s01",
        text="第一部分是背景及技术牵头方，第二部分是核心市场痛点分析。",
        start=0.0,
        end=10.0,
    )
    scene = _run(page, _scene([segment]), settings, store)

    boxes = [a for a in scene.actions if a.target]
    assert [a.target for a in boxes] == ["e1", "e2"]
    assert boxes[0].at < boxes[1].at


def test_a_box_the_sentence_never_mentions_is_dropped(
    settings: Settings, store: ProjectStore
):
    """The camera is bound by what the sentence says.

    A sentence that mentions nothing on the page used to fall through to the
    best-scoring element — a box on a card the narrator is not talking about,
    which is what 「框选跟讲稿对不上」 looks like from the sofa.
    """
    page = _page(
        [
            SlideElement(
                id="e1", kind=ElementKind.PARAGRAPH, text="供应链经营风险可控化",
                bbox=BBox(x=100, y=100, w=300, h=60), importance=0.9,
            ),
            SlideElement(
                id="e2", kind=ElementKind.PARAGRAPH, text="外贸市场拓展精准赋能",
                bbox=BBox(x=100, y=300, w=300, h=60), importance=0.9,
            ),
        ]
    )
    segment = NarrationSegment(
        id="scene_01_s01", text="这一页我们换个角度，先把整体思路说清楚。", start=0.0, end=8.0
    )
    scene = _run(page, _scene([segment]), settings, store)

    assert not [a for a in scene.actions if a.target]


def test_the_box_goes_to_the_text_being_read_not_to_its_label(
    settings: Settings, store: ProjectStore
):
    """「揭榜要求」 is a four-character chip above the paragraph it introduces.

    Two things put the box on it. Ranked by what share of the *element* the
    sentence covers, a four-character chip scores a perfect 1.0 and a
    57-character paragraph scores 15%. And a chip is not what is being said at
    all — it is the name of what is being said, which is underneath it.
    """
    page = _page(
        [
            SlideElement(
                id="chip", kind=ElementKind.PARAGRAPH, text="揭榜要求",
                bbox=BBox(x=100, y=100, w=120, h=40), importance=0.6,
            ),
            SlideElement(
                id="body", kind=ElementKind.PARAGRAPH,
                text="聚焦基地建设任务，重点解决人工智能技术赋能石化化工行业关键问题，以成果落地应用为牵引。",
                bbox=BBox(x=100, y=200, w=700, h=120), importance=0.6,
            ),
        ]
    )
    segment = NarrationSegment(
        id="scene_01_s01",
        text="揭榜要求这一条，是聚焦基地建设任务，以成果落地应用为牵引。",
        start=0.0,
        end=9.0,
    )
    scene = _run(page, _scene([segment]), settings, store)

    # The chip is a label on the block below it, and a label is not the thing
    # being said: the box goes to the paragraph the sentence is quoting, and
    # nowhere at all while the sentence is still naming the label.
    assert [a.target for a in scene.actions if a.target] == ["body"]


def test_a_label_with_nothing_under_it_is_still_a_target(
    settings: Settings, store: ProjectStore
):
    """「(一)背景及技术牵头方」 on a contents page is the content, not a caption.

    What makes a chip a label is not being short — it is being short *and*
    standing on top of something much longer. A contents page is a column of
    short lines with nothing underneath any of them.
    """
    page = _page(
        [
            SlideElement(
                id="one", kind=ElementKind.PARAGRAPH, text="(一)背景及技术牵头方",
                bbox=BBox(x=600, y=200, w=300, h=60), importance=0.8,
            ),
            SlideElement(
                id="two", kind=ElementKind.PARAGRAPH, text="(二)核心市场痛点分析",
                bbox=BBox(x=600, y=400, w=300, h=60), importance=0.8,
            ),
        ]
    )
    segment = NarrationSegment(
        id="scene_01_s01", text="第一部分是背景及技术牵头方。", start=0.0, end=6.0
    )
    scene = _run(page, _scene([segment]), settings, store)

    assert [a.target for a in scene.actions if a.target] == ["one"]


def _two_block_page() -> DocumentPage:
    """Two blocks far enough apart that neither is part of the other."""
    return DocumentPage(
        index=1,
        title="页",
        width=1920,
        height=1080,
        elements=[
            SlideElement(
                id="e1",
                kind=ElementKind.PARAGRAPH,
                text="第一块讲的内容",
                bbox=BBox(x=100, y=100, w=600, h=60),
            ),
            SlideElement(
                id="e2",
                kind=ElementKind.PARAGRAPH,
                text="第二块讲的内容",
                bbox=BBox(x=1100, y=700, w=600, h=60),
            ),
        ],
    )


def test_one_look_per_block_and_at_the_moment_it_is_described():
    """Three complaints, one cause.

    A block was framed at 15s, again at 43s and again at 62s on the same page —
    the camera losing its place and going back. And the one look that survived
    could be the wrong one: a page whose opening sentence says 「用进出口数据找
    海外机会」 brushes past the card headed 「海外机会清单」 nine seconds before
    the script describes it, so keeping the first framed the right card at the
    wrong moment.
    """
    from doc2video.schemas import NarrationSegment
    from doc2video.skills.director import ActionChoice, ActionType, _merge_runs

    segments = {
        "s1": NarrationSegment(id="s1", text="a", start=0.0, end=4.0),
        "s2": NarrationSegment(id="s2", text="b", start=4.0, end=8.0),
        "s3": NarrationSegment(id="s3", text="c", start=8.0, end=12.0),
    }
    brushed = ActionChoice(segment_id="s1", type=ActionType.HIGHLIGHT, target="e1", match=0.18)
    other = ActionChoice(segment_id="s2", type=ActionType.HIGHLIGHT, target="e2", match=0.5)
    described = ActionChoice(segment_id="s3", type=ActionType.HIGHLIGHT, target="e1", match=0.62)

    kept = _merge_runs([brushed, other, described], segments, _two_block_page())

    targets = [c.target for c in kept]
    assert targets.count("e1") == 1, f"同一块只该框一次：{targets}"
    assert [c.segment_id for c in kept if c.target == "e1"] == ["s3"], "该留讲到它的那一次"


def test_a_run_of_the_same_block_still_holds_rather_than_blinking():
    """Consecutive looks are one box that stays up, not one that is redrawn."""
    from doc2video.schemas import NarrationSegment
    from doc2video.skills.director import ActionChoice, ActionType, _merge_runs

    segments = {
        "s1": NarrationSegment(id="s1", text="a", start=0.0, end=4.0),
        "s2": NarrationSegment(id="s2", text="b", start=4.0, end=9.0),
    }
    kept = _merge_runs(
        [
            ActionChoice(segment_id="s1", type=ActionType.HIGHLIGHT, target="e1", match=0.4),
            ActionChoice(segment_id="s2", type=ActionType.HIGHLIGHT, target="e1", match=0.3),
        ],
        segments,
        _two_block_page(),
    )
    assert len(kept) == 1
    assert kept[0].holds_until == 9.0, "连着讲同一块，框就一直框着"


def test_length_stops_deciding_which_block_the_camera_frames():
    """Counting shared pairs hands the page to whichever element has most text.

    All three strings are what was actually on the slide and in the script. The
    sprawling block shares more pairs with the sentence than the card does, so
    by count the block wins — and it did, three separate times on that one
    page, while the card the sentence is naming went unframed.
    """
    from doc2video.skills.director import _match, _shared_grams

    said = '横向覆盖研发、供产销、人才、资金，也就是创新链、产业链、人才链、资金链四条链。'
    card = '研发·供产销·人才·资金'
    sprawl = (
        '构建覆盖石化行业创新链、产业链、人才链、资金链的全链路分层数据集体系，包含预训练'
        '、指令微调、强化学习偏好与基准测试四类数据，支 撑 AI 模型从基础训练到质检评'
        '估的全流程建设，并深度参与石化领域数据标准与接口规范的制定落地。'
    )

    assert _shared_grams(sprawl, said)[0] > _shared_grams(card, said)[0], "按个数是长的赢"
    assert _match(card, said) > _match(sprawl, said), "两边一起比，选真正在讲的那张卡"


def test_the_writers_own_answer_beats_the_cameras_guess():
    """镜头先信讲稿说的，猜是没得信时才用的。

    The writer wrote the sentence with the page's element list in front of it,
    so it knows which line the sentence came from; the camera can only compare
    characters, and on a page whose sections open with the same boilerplate
    that comparison lands on the wrong section as readily as the right one.

    All of the bound ids, not the first: 「落到供应链、外贸、招投标三类情报」 names
    three chips, and a frame around one of them points at a third of what is
    being said.
    """
    from doc2video.schemas import NarrationSegment
    from doc2video.skills.director import DirectorSkill

    page = DocumentPage(
        index=1,
        title="页",
        width=1920,
        height=1080,
        elements=[
            SlideElement(id="chip_a", kind=ElementKind.PARAGRAPH, text="石化供应链情报",
                         bbox=BBox(x=200, y=400, w=200, h=40)),
            SlideElement(id="chip_b", kind=ElementKind.PARAGRAPH, text="石化外贸情报",
                         bbox=BBox(x=440, y=400, w=200, h=40)),
            SlideElement(id="chip_c", kind=ElementKind.PARAGRAPH, text="石化招投标情报",
                         bbox=BBox(x=680, y=400, w=200, h=40)),
            # The one a character-match would reach for, and the wrong answer.
            SlideElement(id="decoy", kind=ElementKind.PARAGRAPH,
                         text="市场研判做供应链、外贸、招投标情报的说明段落，与上一节共用同样的开头套话",
                         bbox=BBox(x=200, y=700, w=900, h=120)),
        ],
    )
    segment = NarrationSegment(
        id="s1",
        text="最上面，市场研判做供应链、外贸、招投标情报。",
        element_refs=["chip_a", "chip_b", "chip_c"],
    )

    skill = DirectorSkill(SkillContext.build(_project_with(page)))
    found = skill._targets_in(segment, page)

    assert len(found) == 1, f"绑定明确时只该有一个框：{found}"
    target, also, _fraction = found[0]
    assert {target, *also} == {"chip_a", "chip_b", "chip_c"}, "三处都要在框里"


def _project_with(page):
    return VideoProject(
        project_id="proj_bound",
        source=Source(type=SourceType.PPTX, file="d.pptx", path="source/d.pptx"),
        document=DocumentModel(pages=[page]),
    )


def test_a_transition_gets_no_frame_when_the_page_bound_the_rest():
    """「过渡句没必要框。」

    An empty `element_refs` is an answer when the page's other sentences carry
    ids: the writer looked and said this one is not about anything on the page.
    Guessing anyway draws a frame over 「先看第一块建设内容」.

    A page where nothing is bound is a page where the step was skipped, and
    then the empty lists mean nothing — there the camera still has to guess.
    """
    from doc2video.schemas import NarrationSegment
    from doc2video.skills.director import DirectorSkill

    page = _two_block_page()
    skill = DirectorSkill(SkillContext.build(_project_with(page)))
    passing = NarrationSegment(id="s2", text="第一块讲的内容说完了，接着看下一块。")

    assert skill._targets_in(passing, page, bound_page=True) == [], "绑过的页面，空就是空"
    assert skill._targets_in(passing, page, bound_page=False), "没绑过的页面还得猜"


def test_a_name_on_its_own_is_not_something_to_frame():
    """「框标题这种是禁止的。」 — judged by what would actually be drawn.

    「再说招募目的。」 announces the next section. A frame around the four
    characters of its name would point at a label; a frame around the label
    *and the block it names* is the section being announced, and that is what
    `focus_box` now draws — the label grows down into its body the same way a
    caption grows up into its figure. So the sentence keeps its gesture, and
    what the test pins is the frame: bigger than the name, never just it.

    Dropping these outright was measured on one deck: thirty bound sentences,
    all category heads — 「供应链情报」「应用方向一」「日报」 — each announced
    while the camera sat still. A name with nothing to grow into (nothing
    under it, chips beside it as short as it is) still gets no frame.
    """
    from doc2video.schemas import BBox, ElementKind, NarrationSegment, SlideElement
    from doc2video.skills.director import ActionType, DirectorSkill

    page = _two_block_page()
    # A name with the paragraph it names underneath it.
    page.elements.append(
        SlideElement(id="head", kind=ElementKind.PARAGRAPH, text="招募目的",
                     bbox=BBox(x=100, y=300, w=140, h=40))
    )
    page.elements.append(
        SlideElement(id="head_body", kind=ElementKind.PARAGRAPH,
                     text="为加快基地建设，依托行业大模型和数据服务平台等能力底座，推进先导场景招募揭榜。",
                     bbox=BBox(x=100, y=350, w=900, h=80))
    )
    page.elements.append(
        SlideElement(id="figure", kind=ElementKind.NUMBER, text="130305家",
                     bbox=BBox(x=100, y=900, w=140, h=40))
    )
    skill = DirectorSkill(SkillContext.build(_project_with(page)))

    from doc2video.schemas import Scene

    def boxes_for(segment):
        scene = Scene(scene_id="sc", source_page=1, narration=segment.text,
                      segments=[segment], duration=12.0)
        return [
            c for c in skill._choose_heuristically(scene, page)
            if c.type in (ActionType.HIGHLIGHT, ActionType.ZOOM)
        ]

    naming = NarrationSegment(id="s1", text="再说招募目的。", element_refs=["head"],
                              start=0.0, end=6.0)
    from doc2video.skills.director import focus_box

    named = boxes_for(naming)
    assert named, "报出名字的那一句，框它名下的整块"
    head = page.element("head")
    drawn = focus_box(head, page)
    assert drawn.w * drawn.h > head.bbox.w * head.bbox.h * 1.5, "画的是块，不是名字"

    # A name with nothing to grow into still gets nothing: chips beside it as
    # short as it is, nothing underneath.
    page.elements.append(
        SlideElement(id="chip", kind=ElementKind.SUBTITLE, text="场景应用层",
                     bbox=BBox(x=1100, y=900, w=140, h=40))
    )
    page.elements.append(
        SlideElement(id="chip2", kind=ElementKind.SUBTITLE, text="模型能力层",
                     bbox=BBox(x=1300, y=900, w=140, h=40))
    )
    bare = NarrationSegment(id="s1b", text="先看场景应用层。", element_refs=["chip"],
                            start=0.0, end=6.0)
    assert boxes_for(bare) == [], "孤立的名字仍然不框"

    # Together with what it names, it is a frame again.
    both = NarrationSegment(id="s2", text="招募目的讲的是第一块内容，说清楚为什么招募。",
                            element_refs=["head", "e1"], start=0.0, end=6.0)
    assert boxes_for(both), "名字加它领的内容，可以框"

    # A figure is short and is the whole point of pointing at it.
    counted = NarrationSegment(id="s3", text="上面挂着 130305 家企业，覆盖整条产业链。",
                               element_refs=["figure"], start=0.0, end=6.0)
    assert boxes_for(counted), "数字不算标题"


def _card_walk_page() -> DocumentPage:
    """A category card: label, its body under it, and a second card below."""
    return DocumentPage(
        index=1,
        title="页",
        width=1920,
        height=1080,
        elements=[
            SlideElement(id="lbl1", kind=ElementKind.PARAGRAPH, text="企业挂接",
                         bbox=BBox(x=130, y=300, w=120, h=40)),
            SlideElement(id="body1", kind=ElementKind.PARAGRAPH,
                         text="主营产品、产能项目、区域布局、产业位置和竞合关系，形成企业档案。",
                         bbox=BBox(x=130, y=350, w=900, h=40)),
            SlideElement(id="lbl2", kind=ElementKind.PARAGRAPH, text="技术挂接",
                         bbox=BBox(x=130, y=500, w=120, h=40)),
            SlideElement(id="body2", kind=ElementKind.PARAGRAPH,
                         text="技术方向、专利成果、研发主体、成熟度和演进趋势，形成技术档案。",
                         bbox=BBox(x=130, y=550, w=900, h=40)),
        ],
    )


def test_a_bound_paraphrase_is_not_undone_by_the_overlap_check():
    """「链上挂企业。」 says 「企业挂接」 in other words — which is the job.

    `_check_and_redo` re-derives every action from shared characters, and for
    a target the writer bound that is the very reverse-engineering the bound
    branch of `_targets_in` exists to bypass. The paraphrase read as a miss,
    the binding was undone, and on one deck the camera lost every category
    head whose sentence did not quote the slide: a 35-second hole in the
    middle of a page that walks four cards.
    """
    from doc2video.schemas import NarrationSegment, Scene
    from doc2video.skills.director import DirectorSkill

    page = _card_walk_page()
    skill = DirectorSkill(SkillContext.build(_project_with(page)))
    scene = Scene(
        scene_id="sc", source_page=1, narration="链上挂企业。再挂技术。",
        segments=[
            NarrationSegment(id="s1", text="链上挂企业。", element_refs=["lbl1"],
                             start=0.0, end=6.0),
            NarrationSegment(id="s2", text="再挂技术。", element_refs=["lbl2"],
                             start=6.0, end=12.0),
        ],
        duration=12.0,
    )
    scene.actions = skill._to_actions(scene, page, skill._choose_heuristically(scene, page))
    kept = skill._check_and_redo(scene, page)
    targets = {a.target for a in kept if a.target}
    assert "lbl1" in targets and "lbl2" in targets, "写手绑定的换说法不能被字面比对否决"


def test_a_run_on_one_block_is_one_hold_for_the_whole_run():
    """Three sentences on one paragraph are one box that stays up.

    Two bugs hid here. `next_at` was built before `_merge_runs` on pre-merge
    indices, so a lookup on the merged list could hit a stale neighbour and
    truncate a hold at the very sentence it was holding for — 「产业链结构」
    kept 5.7 of its 11.4 seconds. And a run whose first sentence carried a
    companion (`also`) drew a slightly different frame from the bare ones
    after it, so the keys differed, the run broke, and the rest was dropped
    as a repeat: the box left at the first full stop while the narrator
    stayed on the block to 51s.
    """
    from doc2video.schemas import NarrationSegment, Scene
    from doc2video.skills.director import DirectorSkill

    page = _card_walk_page()
    skill = DirectorSkill(SkillContext.build(_project_with(page)))
    scene = Scene(
        scene_id="sc", source_page=1, narration="三句话都在讲同一块。",
        segments=[
            NarrationSegment(id="s1", text="先说企业挂接这一层。",
                             element_refs=["lbl1", "body1"], start=0.0, end=6.0),
            NarrationSegment(id="s2", text="主营产品、产能项目和区域布局都挂上。",
                             element_refs=["body1"], start=6.0, end=12.0),
            NarrationSegment(id="s3", text="产业位置和竞合关系也在这一层。",
                             element_refs=["body1"], start=12.0, end=18.0),
        ],
        duration=18.0,
    )
    actions = [
        a for a in skill._to_actions(scene, page, skill._choose_heuristically(scene, page))
        if a.type.value != "transition"
    ]
    assert len(actions) == 1, "同一块上的连续三句是一次注视，不是三次"
    action = actions[0]
    assert action.at + action.duration >= 16.0, "框要停到这一串讲完，不是第一句完就下"
