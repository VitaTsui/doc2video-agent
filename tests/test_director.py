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


def test_small_target_still_zooms(settings: Settings, store: ProjectStore):
    page = _page([_element("e_tiny", BBox(x=400, y=400, w=150, h=100))])
    scene = _scene(
        [
            NarrationSegment(id="s1", text="讲 e_tiny", element_refs=["e_tiny"],
                             emphasis=True, start=0.0, end=6.0)
        ]
    )
    result = _run(page, scene, settings, store)
    assert any(a.type is ActionType.ZOOM for a in result.actions if a.target == "e_tiny")


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


def test_a_wall_of_text_is_outlined_rather_than_zoomed_into():
    """A zoom says "look closely at this", and a paragraph does not reward it.

    Enlarged, a paragraph is still a paragraph: the narrator is not reading it,
    and the viewer gets a slow push into a wall of words. Measured on a
    finished video — eleven of twenty-six zooms landed on blocks over forty
    characters, the largest 342.

    A picture or a chart is the opposite case and keeps its zoom: those are
    exactly the things worth filling the frame with.
    """
    from doc2video.schemas import BBox, DocumentPage, ElementKind, NarrationSegment, SlideElement
    from doc2video.skills.director import MAX_ZOOM_CHARS, DirectorSkill

    def element(text: str, kind: ElementKind = ElementKind.PARAGRAPH) -> SlideElement:
        return SlideElement(
            id="e1", kind=kind, text=text, bbox=BBox(x=0, y=0, w=300, h=80), label="e1"
        )

    page = DocumentPage(index=1, width=1920, height=1080)
    stressed = NarrationSegment(id="s1", text="这一句是重点。", emphasis=True, start=0.0, end=3.0)

    short = element("十六个数据来源")
    long = element("很长的一段正文，" * 12)
    assert len(long.text) > MAX_ZOOM_CHARS

    assert DirectorSkill._pick_action(stressed, short, page) is ActionType.ZOOM
    assert DirectorSkill._pick_action(stressed, long, page) is ActionType.HIGHLIGHT
    # A picture has no text at all and is still worth filling the frame with.
    assert DirectorSkill._pick_action(stressed, element("", ElementKind.CHART), page) is (
        ActionType.ZOOM
    )


def test_a_target_too_small_to_be_worth_the_crop_is_not_zoomed():
    """A zoom trades the page for the target, and sometimes gets nothing back.

    The page around a label is what tells the viewer where the label is. Push
    in and that goes away — worth it only if the target ends up big enough to
    look at. For a small one it never does: at the renderer's largest push, a
    box covering two thousandths of the page still covers under two percent of
    the frame. Measured on a real deck, eight of twenty-six zooms were that.
    """
    from doc2video.schemas import BBox, DocumentPage, NarrationSegment, SlideElement
    from doc2video.skills.director import DirectorSkill, _zoom_pays_off

    page = DocumentPage(index=1, width=1920, height=1080)

    def element(w: float, h: float) -> SlideElement:
        return SlideElement(
            id="e1", text="专报", bbox=BBox(x=100, y=100, w=w, h=h), label="专报"
        )

    tiny = element(60, 40)  # 0.12% of the page
    fair = element(600, 300)  # 8.7%

    assert not _zoom_pays_off(tiny, page)
    assert _zoom_pays_off(fair, page)

    stressed = NarrationSegment(id="s1", text="这里是重点。", emphasis=True, start=0, end=3)
    assert DirectorSkill._pick_action(stressed, tiny, page) is not ActionType.ZOOM
    assert DirectorSkill._pick_action(stressed, fair, page) is ActionType.ZOOM


def test_the_largest_push_matches_what_the_renderer_will_do():
    """The decision is made against a number the renderer owns.

    Choosing a zoom because the target *would* be big enough at 3× and then
    having the camera stop at 1.6× is deciding against a video nobody sees.
    """
    from pathlib import Path

    from doc2video.skills.director import RENDER_MAX_SCALE

    source = Path("renderer/src/components/useCameraTransform.ts").read_text(encoding="utf-8")
    assert f"const MAX_SCALE = {int(RENDER_MAX_SCALE)}" in source


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
