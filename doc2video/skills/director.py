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
POINTER_DURATION = 1.4
# Let the viewer hear the sentence start before the camera moves.
AUDIO_LEAD = 0.3
ZOOM_KINDS = {ElementKind.NUMBER, ElementKind.CHART, ElementKind.TABLE, ElementKind.IMAGE}


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
        scored: list[tuple[float, ActionChoice]] = []
        for segment in scene.segments:
            target_id = self._resolve_target(segment, page)
            if target_id is None:
                continue
            element = page.element(target_id)
            if element is None:
                continue
            score = element.importance + (0.5 if segment.emphasis else 0.0)
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
            return ActionType.ZOOM
        span = max(segment.end - segment.start, 0.0)
        if span and span <= MAX_POINTER_SPAN and _coverage(element, page) <= MAX_POINTER_COVERAGE:
            return ActionType.POINTER
        return ActionType.HIGHLIGHT

    def _resolve_target(self, segment: NarrationSegment, page: DocumentPage) -> str | None:
        for ref in segment.element_refs:
            if page.element(ref) is not None:
                return ref
        # Nothing bound: fall back to the most distinctive element whose text the
        # sentence actually mentions, so we never point at an unrelated box.
        best: tuple[float, str] | None = None
        for element in page.elements:
            if element.kind is ElementKind.TITLE or not element.text:
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


def _coverage(element, page: DocumentPage) -> float:
    """Share of the page area a element occupies, 0..1."""
    if not page.width or not page.height:
        return 1.0
    return (element.bbox.w * element.bbox.h) / (page.width * page.height)
