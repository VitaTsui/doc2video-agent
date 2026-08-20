"""presentation-director — narration semantics to visual attention.

The core of the system (方案 §10). The model decides *what should be looked at*;
this module decides *when*, deriving every timestamp from the TTS timeline
rather than from the model. That split is why the result is reproducible: the
same project renders to the same frames, and a re-voiced scene re-times its
actions automatically.
"""

from __future__ import annotations

from pydantic import BaseModel

from ..schemas import (
    ActionType,
    DirectorAction,
    DocumentPage,
    ElementKind,
    NarrationSegment,
    PageType,
    Scene,
)
from .base import Skill

# Rhythm guardrails: too many camera moves is as bad as none (方案 §10 判断原则).
MAX_ACTIONS_PER_SCENE = 4
MIN_ACTION_DURATION = 1.2
MAX_ACTION_DURATION = 4.0
# Below this an action flashes rather than reads; drop it instead of squeezing it.
MIN_KEEP_DURATION = 0.6
RESET_DURATION = 1.0
# A target covering more of the page than this is not worth zooming into.
MAX_ZOOM_COVERAGE = 0.35
# A pointer is a quick "look here" beat: it suits a passing mention of a small,
# precise target. Dwelling on something calls for a highlight or a zoom instead.
MAX_POINTER_COVERAGE = 0.10
MAX_POINTER_SPAN = 2.6
# A zoom says "look closely at this". Past this much text there is nothing to
# look closely at — a paragraph enlarged is still a paragraph, the narrator is
# not reading it, and the viewer gets a slow push into a wall of words. Found
# in a finished video: eleven of twenty-six zooms landed on blocks over forty
# characters, the largest of them 342. Those become outlines instead, which
# say "this block" without promising it is worth enlarging.
MAX_ZOOM_CHARS = 40
# The renderer will not push past this, so a target's size after zooming is
# known before the zoom is chosen. Kept in step with `MAX_SCALE` in
# `renderer/src/components/useCameraTransform.ts`; a test asserts they agree.
RENDER_MAX_SCALE = 3.0
# How much of the frame a target has to fill once the camera has done all it
# can. Below this the push buys nothing: the label is still too small to read
# and the page around it — which is what told the viewer where the label was —
# has been cropped away. Measured on a real deck: eight of twenty-six zooms
# landed on things that stayed under five percent of the frame at full scale.
MIN_ZOOM_RESULT = 0.05
POINTER_DURATION = 1.4
# Let the viewer hear the sentence start before the camera moves.
AUDIO_LEAD = 0.3
ZOOM_KINDS = {ElementKind.NUMBER, ElementKind.CHART, ElementKind.TABLE, ElementKind.IMAGE}

# Pages that are signposts rather than content: a cover, the table of contents,
# a section divider. Every one of them says the same thing — "here is where we
# are" — and there is nothing on them to point at. Boxing something anyway is
# how a deck ended up with a highlight around the word 「CONTENTS」 and around
# the numeral 「1」 on a divider. The page change is the whole gesture.
SIGNPOST_PAGES = {PageType.COVER, PageType.AGENDA, PageType.SECTION}

# Text with nothing in it worth looking at. A target has to carry information:
# a lone digit is the section's number, 「CONTENTS」 is the word above the list,
# and a box around either points the viewer at furniture.
DECORATIVE_TEXT = {
    "contents", "content", "agenda", "outline", "index", "目录", "大纲", "议程",
    "thank you", "thanks", "谢谢", "感谢", "汇报人", "报告人", "logo",
}
MIN_TARGET_CHARS = 2


class ActionChoice(BaseModel):
    segment_id: str
    type: ActionType
    target: str


class SceneDirection(BaseModel):
    actions: list[ActionChoice]


class DirectorSkill(Skill):
    name = "presentation-director"
    description = "把语言语义转换为视觉注意力和镜头动作"

    def run(self) -> None:
        document = self.project.document
        total_actions = 0

        for scene in self.project.scenes:
            page = document.page(scene.source_page) if scene.source_page else None
            if page is None:
                scene.actions = []
                continue
            choices = self._choose_heuristically(scene, page)
            scene.actions = self._to_actions(scene, page, choices)
            total_actions += len(scene.actions)

        self.log.info("镜头设计完成：共 %d 个动作", total_actions)

    # -- choosing what to look at ---------------------------------------
    def _choose_heuristically(self, scene: Scene, page: DocumentPage) -> list[ActionChoice]:
        """Rank segments by how much they deserve a camera move, then take the top few."""
        # A signpost page gets its transition and nothing else.
        if page.page_type in SIGNPOST_PAGES:
            return []

        scored: list[tuple[float, ActionChoice]] = []
        for segment in scene.segments:
            target_id = self._resolve_target(segment, page)
            if target_id is None:
                continue
            element = page.element(target_id)
            if element is None or not _worth_pointing_at(element) or _is_banner(element, page):
                continue
            score = element.importance + (0.5 if segment.emphasis else 0.0)
            # A node in a declared flow is worth more than a box that happens
            # to be on the page: the arrows say this one is part of the thing
            # being explained.
            if page.diagram is not None and target_id in page.diagram.nodes:
                score += 0.3
            action_type = self._pick_action(segment, element, page)
            choice = ActionChoice(segment_id=segment.id, type=action_type, target=target_id)
            scored.append((score, choice))

        scored.sort(key=lambda item: -item[0])
        # One move per element: repeatedly zooming the same box reads as a stutter,
        # not as emphasis.
        chosen: list[ActionChoice] = []
        seen_targets: set[str] = set()
        for _, choice in scored:
            if choice.target in seen_targets:
                continue
            seen_targets.add(choice.target)
            chosen.append(choice)
            if len(chosen) >= MAX_ACTIONS_PER_SCENE:
                break
        # Keep narrative order — ranking was only for selection.
        order = {s.id: i for i, s in enumerate(scene.segments)}
        chosen.sort(key=lambda c: order.get(c.segment_id, 0))

        if sum(1 for c in chosen if c.type is ActionType.ZOOM) >= 2 and scene.segments:
            chosen.append(
                ActionChoice(segment_id=scene.segments[-1].id, type=ActionType.RESET, target="")
            )
        return chosen

    @staticmethod
    def _pick_action(segment: NarrationSegment, element, page: DocumentPage) -> ActionType:
        """Match the gesture to what the sentence is doing.

        Emphasis and data-bearing elements earn a zoom; a brief mention of a
        small element earns a pointer; anything else gets an outline. Without
        the pointer branch the director had only two gestures, so quick
        name-checks came out looking like the same emphasis as a key figure.
        """
        if segment.emphasis or element.kind in ZOOM_KINDS:
            # Unless it is a wall of text: a picture or a number rewards being
            # enlarged, a paragraph does not.
            wordy = element.kind not in ZOOM_KINDS and len(element.text or "") > MAX_ZOOM_CHARS
            if not wordy and _zoom_pays_off(element, page):
                return ActionType.ZOOM
            return ActionType.HIGHLIGHT
        span = max(segment.end - segment.start, 0.0)
        if span and span <= MAX_POINTER_SPAN and _coverage(element, page) <= MAX_POINTER_COVERAGE:
            return ActionType.POINTER
        return ActionType.HIGHLIGHT

    def _resolve_target(self, segment: NarrationSegment, page: DocumentPage) -> str | None:
        for ref in segment.element_refs:
            element = page.element(ref)
            if element is None or not _worth_pointing_at(element):
                continue
            if not _is_banner(element, page):
                return ref
        # Nothing bound: fall back to the most distinctive element whose text the
        # sentence actually mentions, so we never point at an unrelated box.
        best: tuple[float, str] | None = None
        for element in page.elements:
            if element.kind is ElementKind.TITLE or not _worth_pointing_at(element):
                continue
            key = element.text.strip()[:6]
            if len(key) >= 2 and key in segment.text:
                score = element.importance
                if best is None or score > best[0]:
                    best = (score, element.id)
        return best[1] if best else None

    @staticmethod
    def _fit_action_type(choice: ActionChoice, page: DocumentPage) -> ActionType:
        """Downgrade a zoom whose target already fills most of the page.

        Zooming to a box that covers half the slide magnifies everything and
        singles out nothing — an outline communicates the same thing without
        throwing away context.
        """
        if choice.type not in (ActionType.ZOOM, ActionType.POINTER) or not choice.target:
            return choice.type
        element = page.element(choice.target)
        if element is None or not page.width or not page.height:
            return choice.type
        coverage = _coverage(element, page)
        if choice.type is ActionType.POINTER:
            # Pointing at a box that fills the page indicates nothing.
            return ActionType.HIGHLIGHT if coverage > MAX_POINTER_COVERAGE else ActionType.POINTER
        return ActionType.HIGHLIGHT if coverage > MAX_ZOOM_COVERAGE else ActionType.ZOOM

    # -- turning choices into timed actions -------------------------------
    def _to_actions(
        self, scene: Scene, page: DocumentPage, choices: list[ActionChoice]
    ) -> list[DirectorAction]:
        segments = {s.id: s for s in scene.segments}
        actions: list[DirectorAction] = []

        # Page entry: every scene opens with a transition so cuts never feel abrupt.
        actions.append(
            DirectorAction(
                at=0.0,
                type=ActionType.TRANSITION,
                effect="fade" if page.page_type is not PageType.COVER else "fade-slow",
                duration=0.5,
            )
        )

        last_target: str | None = None
        for choice in choices:
            segment = segments.get(choice.segment_id)
            if segment is None:
                continue

            if choice.type is ActionType.RESET:
                # Reset belongs at the tail of the scene, returning to the full page.
                duration = min(RESET_DURATION, max(0.4, scene.duration * 0.15))
                at = max(0.0, scene.duration - duration)
            else:
                if choice.target and choice.target == last_target:
                    continue
                span = max(segment.end - segment.start, 0.0)
                if choice.type is ActionType.POINTER:
                    duration = min(POINTER_DURATION, max(MIN_KEEP_DURATION, span - 0.2))
                else:
                    duration = max(MIN_ACTION_DURATION, min(MAX_ACTION_DURATION, span - 0.2))
                at = max(0.0, segment.start + AUDIO_LEAD)

            # An action must fit inside its own scene, and be long enough to read.
            duration = min(duration, scene.duration - at)
            if at >= scene.duration or duration < MIN_KEEP_DURATION:
                continue
            last_target = choice.target or last_target

            action_type = self._fit_action_type(choice, page)
            effect = {
                ActionType.ZOOM: "zoom-highlight",
                ActionType.HIGHLIGHT: "outline",
                ActionType.POINTER: "pointer",
                ActionType.RESET: "ease-out",
            }.get(action_type, "")

            actions.append(
                DirectorAction(
                    at=round(at, 2),
                    type=action_type,
                    target=choice.target or None,
                    effect=effect,
                    duration=round(duration, 2),
                    params={"segment_id": choice.segment_id},
                )
            )

        actions.sort(key=lambda a: a.at)
        return actions


def _zoom_pays_off(element, page: DocumentPage) -> bool:
    """Whether pushing in on this actually shows the viewer anything.

    A zoom trades context for size: the page around the target is what said
    where the target was, and the camera crops it away. That trade is only
    worth making if the target ends up big enough to be worth looking at, and
    for a small label it never does — at the renderer's largest push a box
    covering two thousandths of the page still covers under two percent of the
    frame. The viewer loses the page and gains nothing.
    """
    coverage = _coverage(element, page)
    return coverage * RENDER_MAX_SCALE**2 >= MIN_ZOOM_RESULT


def _is_banner(element, page: DocumentPage) -> bool:
    """Whether this element is the page's own heading.

    Pointing at it says nothing. The viewer is already on the page, and its
    title is what the whole page is about — a box drawn around it reads as the
    camera having nowhere better to go. Found in a finished video: three shots
    aimed at the page title, two of them at text identical to it.

    The fallback branch of `_resolve_target` had always skipped titles; the
    branch that takes the model's own `element_refs` did not, so a heading the
    model happened to bind to went straight through.
    """
    if element.kind is ElementKind.TITLE:
        return True
    text = (element.text or "").strip()
    return bool(text) and text == (page.title or "").strip()


def _worth_pointing_at(element) -> bool:
    """Whether there is anything on this element worth a viewer's eye.

    Non-text elements — a chart, an image, a table — are always worth it; they
    are the picture. Text has to say something: a lone digit or a stock heading
    is page furniture, and a box drawn around furniture reads as a mistake even
    when the sentence it belongs to is right.
    """
    if element.kind in ZOOM_KINDS and element.kind is not ElementKind.NUMBER:
        return True
    text = (element.text or "").strip()
    if len(text) < MIN_TARGET_CHARS:
        return False
    if text.lower().strip(" .、·:：") in DECORATIVE_TEXT:
        return False
    # Digits and punctuation only: a section number, a page number, a bullet.
    return any(ch.isalpha() or "\u4e00" <= ch <= "\u9fa5" for ch in text)


def _coverage(element, page: DocumentPage) -> float:
    """Share of the page area a element occupies, 0..1."""
    if not page.width or not page.height:
        return 1.0
    return (element.bbox.w * element.bbox.h) / (page.width * page.height)
