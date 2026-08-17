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
from ..tools.llm import model_schema
from .base import Skill, load_prompt

# Rhythm guardrails: too many camera moves is as bad as none (方案 §10 判断原则).
MAX_ACTIONS_PER_SCENE = 4
MIN_ACTION_DURATION = 1.2
MAX_ACTION_DURATION = 4.0
# Below this an action flashes rather than reads; drop it instead of squeezing it.
MIN_KEEP_DURATION = 0.6
RESET_DURATION = 1.0
# A target covering more of the page than this is not worth zooming into.
MAX_ZOOM_COVERAGE = 0.35
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
            choices = self.try_llm(
                lambda s=scene, p=page: self._choose_with_llm(s, p),
                lambda s=scene, p=page: self._choose_heuristically(s, p),
                what=f"镜头设计（{scene.scene_id}）",
            )
            scene.actions = self._to_actions(scene, page, choices)
            total_actions += len(scene.actions)

        self.log.info("镜头设计完成：共 %d 个动作", total_actions)

    # -- choosing what to look at ---------------------------------------
    def _choose_with_llm(self, scene: Scene, page: DocumentPage) -> list[ActionChoice]:
        prompt = self._render_prompt(scene, page)
        raw = self.llm.complete_json(
            prompt, schema=model_schema(SceneDirection), system=load_prompt("director")
        )
        result = SceneDirection.model_validate(raw)

        valid_segments = {s.id for s in scene.segments}
        valid_targets = {e.id for e in page.elements}
        cleaned: list[ActionChoice] = []
        for choice in result.actions:
            if choice.segment_id not in valid_segments:
                continue
            if choice.type is ActionType.RESET:
                cleaned.append(choice)
                continue
            if choice.target in valid_targets:
                cleaned.append(choice)
        return cleaned[: MAX_ACTIONS_PER_SCENE + 1]

    def _render_prompt(self, scene: Scene, page: DocumentPage) -> str:
        lines = [
            f"场景 {scene.scene_id}（第 {page.index} 页，{page.page_type}）",
            f"页面标题：{page.title}",
            f"场景总时长：{scene.duration:.1f} 秒",
            "",
            "页面元素：",
        ]
        for element in sorted(page.elements, key=lambda e: -e.importance):
            box = element.bbox
            text = element.text.replace("\n", " ")[:80]
            lines.append(
                f"- [{element.id}] ({element.kind}, 重要性 {element.importance:.1f}) "
                f"位置 x={box.x:.0f} y={box.y:.0f} w={box.w:.0f} h={box.h:.0f}｜{text}"
            )
        lines.append("\n讲稿片段：")
        for segment in scene.segments:
            refs = "、".join(segment.element_refs) or "未绑定"
            lines.append(
                f"- [{segment.id}] {segment.start:.1f}s–{segment.end:.1f}s"
                f"{'（强调）' if segment.emphasis else ''} 初步绑定：{refs}\n  {segment.text}"
            )
        lines.append("\n请给出这个场景的镜头动作。")
        return "\n".join(lines)

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
            action_type = (
                ActionType.ZOOM
                if segment.emphasis or element.kind in ZOOM_KINDS
                else ActionType.HIGHLIGHT
            )
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
        if choice.type is not ActionType.ZOOM or not choice.target:
            return choice.type
        element = page.element(choice.target)
        if element is None or not page.width or not page.height:
            return choice.type
        coverage = (element.bbox.w * element.bbox.h) / (page.width * page.height)
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
