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
