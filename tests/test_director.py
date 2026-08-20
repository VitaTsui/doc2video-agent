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
    """A cover, a table of contents, a section divider — nothing to point at.

    Every one of them says the same thing: here is where we are. A deck went
    out with a highlight around the word 「CONTENTS」 and around the numeral on
    a divider, and both read as the camera pointing at furniture. The page
    change is the whole gesture.
    """
    from doc2video.schemas import PageType
    from doc2video.skills.director import SIGNPOST_PAGES

    assert {PageType.COVER, PageType.AGENDA, PageType.SECTION} == SIGNPOST_PAGES


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
