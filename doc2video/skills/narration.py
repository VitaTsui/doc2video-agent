"""presentation-narration — write the script, under a duration budget.

Duration control happens here, not after TTS: each page gets a character budget
derived from the target total duration, and the script is written to fit it.
Fixing length afterwards would mean re-voicing everything.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from ..core.ids import scene_id
from ..schemas import DocumentPage, NarrationSegment, PageType, Scene, SceneVisual, VisualType
from ..tools.llm import model_schema
from ..tools.tts import estimate_duration
from .base import Skill, load_prompt

BATCH_SIZE = 4

# Per-page-type weights for splitting the total duration budget.
TYPE_WEIGHT = {
    PageType.COVER: 0.35,
    PageType.AGENDA: 0.45,
    PageType.SECTION: 0.4,
    PageType.CONTENT: 1.0,
    PageType.CHART: 1.25,
    PageType.ARCHITECTURE: 1.35,
    PageType.COMPARISON: 1.15,
    PageType.CASE: 1.15,
    PageType.SUMMARY: 0.8,
    PageType.CONTACT: 0.25,
    PageType.OTHER: 0.9,
}
EMPHASIS_MULTIPLIER = 1.9
MIN_SCENE_SECONDS = 4.0
MAX_SCENE_SECONDS = 75.0

SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])\s*|\n+")

TRANSITIONS = [
    "我们先从{title}说起。",
    "接着来看{title}。",
    "在此基础上，{title}。",
    "说到这里，就要提到{title}。",
    "顺着这个思路，{title}。",
]


class SegmentDraft(BaseModel):
    text: str
    element_refs: list[str]
    emphasis: bool


class PageNarration(BaseModel):
    index: int
    narration: str
    segments: list[SegmentDraft]


class NarrationResult(BaseModel):
    pages: list[PageNarration]


class NarrationSkill(Skill):
    name = "presentation-narration"
    description = "生成适合目标受众和时长的讲稿"

    def run(self) -> None:
        document = self.project.document
        intent = self.project.intent
        pages = [p for p in document.ordered_pages() if p.index not in intent.skip_pages]
        if not pages:
            self.log.warning("没有可讲解的页面")
            return

        budgets = self._allocate_budget(pages)
        drafts = self.try_llm(
            lambda: self._write_with_llm(pages, budgets),
            lambda: self._write_heuristically(pages, budgets),
            what="讲稿生成",
        )
        self._build_scenes(pages, drafts)
        self.log.info(
            "讲稿生成完成：%d 个场景，预计 %.1f 秒",
            len(self.project.scenes),
            self.project.total_duration(),
        )

    def rewrite_scene(self, scene: Scene, instruction: str, target_seconds: float | None) -> None:
        """Rewrite one scene's script in place — the chat-edit entry point.

        Only this scene changes, which is what keeps a "page 7 is too long" edit
        from re-voicing and re-rendering the whole video (方案 §9).
        """
        page = self.project.document.page(scene.source_page) if scene.source_page else None
        if page is None:
            self.log.warning("场景 %s 没有对应页面，跳过改写", scene.scene_id)
            return

        seconds = target_seconds or scene.duration or MIN_SCENE_SECONDS
        seconds = max(MIN_SCENE_SECONDS, min(MAX_SCENE_SECONDS, seconds))
        budgets = {page.index: seconds}

        def with_llm() -> dict[int, PageNarration]:
            return self._write_with_llm_single(page, budgets, instruction)

        drafts = self.try_llm(
            with_llm,
            lambda: self._write_heuristically([page], budgets),
            what=f"改写讲稿（{scene.scene_id}）",
        )
        draft = drafts.get(page.index)
        if draft is None:
            return

        valid_ids = {e.id for e in page.elements}
        scene.narration = draft.narration.strip()
        scene.segments = [
            NarrationSegment(
                id=f"{scene.scene_id}_s{seq:02d}",
                text=seg.text.strip(),
                element_refs=[ref for ref in seg.element_refs if ref in valid_ids],
                emphasis=seg.emphasis,
            )
            for seq, seg in enumerate(draft.segments, start=1)
            if seg.text.strip()
        ] or self._split_into_segments(scene.narration, scene.scene_id)
        scene.duration = estimate_duration(scene.narration, self.ctx.settings.tts_speech_rate)

    def _write_with_llm_single(
        self, page: DocumentPage, budgets: dict[int, float], instruction: str
    ) -> dict[int, PageNarration]:
        prompt = self._render_prompt([page], budgets, previous_tail="")
        prompt += f"\n\n用户对这一页的修改要求：{instruction}"
        raw = self.llm.complete_json(
            prompt, schema=model_schema(NarrationResult), system=load_prompt("narration")
        )
        result = NarrationResult.model_validate(raw)
        return {p.index: p for p in result.pages}

    # -- duration budget ------------------------------------------------
    def _allocate_budget(self, pages: list[DocumentPage]) -> dict[int, float]:
        intent = self.project.intent
        weights: dict[int, float] = {}
        for page in pages:
            weight = TYPE_WEIGHT.get(page.page_type, 1.0)
            # More real content on a page deserves proportionally more airtime.
            content_factor = 1.0 + min(len(page.raw_text()), 600) / 600
            weight *= content_factor
            if page.index in intent.emphasis_pages:
                weight *= EMPHASIS_MULTIPLIER
            weights[page.index] = weight

        total_weight = sum(weights.values()) or 1.0
        budgets: dict[int, float] = {}
        for index, weight in weights.items():
            seconds = intent.duration * weight / total_weight
            budgets[index] = max(MIN_SCENE_SECONDS, min(MAX_SCENE_SECONDS, seconds))
        return budgets

    @staticmethod
    def _char_budget(seconds: float) -> int:
        # Mirrors the TTS estimator so the written script and the spoken clip agree.
        return int(seconds * 4.6)

    # -- LLM path -------------------------------------------------------
    def _write_with_llm(
        self, pages: list[DocumentPage], budgets: dict[int, float]
    ) -> dict[int, PageNarration]:
        schema = model_schema(NarrationResult)
        system = load_prompt("narration")
        drafts: dict[int, PageNarration] = {}
        previous_tail = ""

        for start in range(0, len(pages), BATCH_SIZE):
            batch = pages[start : start + BATCH_SIZE]
            prompt = self._render_prompt(batch, budgets, previous_tail)
            raw = self.llm.complete_json(prompt, schema=schema, system=system)
            result = NarrationResult.model_validate(raw)
            for page_narration in result.pages:
                drafts[page_narration.index] = page_narration
            if result.pages:
                previous_tail = result.pages[-1].narration[-120:]
        return drafts

    def _render_prompt(
        self, batch: list[DocumentPage], budgets: dict[int, float], previous_tail: str
    ) -> str:
        document = self.project.document
        intent = self.project.intent
        lines = [
            f"文档主题：{document.topic or document.title}",
            f"文档摘要：{document.summary}",
            f"关键概念：{'、'.join(document.key_concepts) or '（无）'}",
            "",
            f"目标观众：{intent.audience}",
            f"风格：{intent.style}｜语气：{intent.tone}",
            f"全片目标时长：{intent.duration} 秒",
        ]
        if intent.instructions:
            lines.append(f"用户额外要求：{intent.instructions}")
        if previous_tail:
            lines.append(f"\n上一页讲稿结尾（用于自然衔接）：{previous_tail}")

        lines.append("\n以下是本批次要写讲稿的页面：")
        for page in batch:
            seconds = budgets[page.index]
            lines.append(f"\n## 第 {page.index} 页（{page.page_type}）")
            lines.append(f"标题：{page.title}")
            if page.summary:
                lines.append(f"页面要点：{page.summary}")
            if page.key_points:
                lines.append("关键点：" + "；".join(page.key_points))
            if page.speaker_notes:
                lines.append(f"演讲者备注：{page.speaker_notes}")
            lines.append(
                f"字数预算：约 {self._char_budget(seconds)} 字（对应 {seconds:.0f} 秒）"
            )
            lines.append("可绑定的元素：")
            for element in sorted(page.elements, key=lambda e: -e.importance)[:12]:
                text = element.text.replace("\n", " ")[:80]
                lines.append(
                    f"- [{element.id}] ({element.kind}, "
                    f"重要性 {element.importance:.1f}) {text}"
                )
        return "\n".join(lines)

    # -- heuristic path -------------------------------------------------
    def _write_heuristically(
        self, pages: list[DocumentPage], budgets: dict[int, float]
    ) -> dict[int, PageNarration]:
        drafts: dict[int, PageNarration] = {}
        for order, page in enumerate(pages):
            sentences = self._heuristic_sentences(page, order, budgets[page.index])
            segments = [
                SegmentDraft(
                    text=text,
                    element_refs=refs,
                    emphasis=bool(refs) and idx > 0,
                )
                for idx, (text, refs) in enumerate(sentences)
            ]
            drafts[page.index] = PageNarration(
                index=page.index,
                narration="".join(s.text for s in segments),
                segments=segments,
            )
        return drafts

    def _heuristic_sentences(
        self, page: DocumentPage, order: int, seconds: float
    ) -> list[tuple[str, list[str]]]:
        title = page.title or f"第 {page.index} 页"
        sentences: list[tuple[str, list[str]]] = []

        if page.page_type is PageType.COVER:
            topic = self.project.document.topic or title
            sentences.append((f"这一期我们来讲{topic}。", []))
        else:
            sentences.append((TRANSITIONS[order % len(TRANSITIONS)].format(title=title), []))

        ranked = sorted(page.elements, key=lambda e: -e.importance)
        budget_chars = self._char_budget(seconds)
        used = sum(len(text) for text, _ in sentences)

        for point in page.key_points:
            if used >= budget_chars:
                break
            target = next(
                (e.id for e in ranked if point[:8] and point[:8] in e.text),
                ranked[0].id if ranked else None,
            )
            text = f"{point}。"
            sentences.append((text, [target] if target else []))
            used += len(text)

        # Speaker notes are the closest thing a deck has to an explanation of
        # itself — use them to fill the remaining budget before padding.
        if used < budget_chars and page.speaker_notes:
            note = page.speaker_notes.strip().replace("\n", "")[: budget_chars - used]
            if len(note) >= 6:
                sentences.append((f"{note}。", []))
                used += len(note)

        if len(sentences) == 1 and page.summary:
            sentences.append((f"{page.summary}。", []))
        return sentences

    # -- scene assembly -------------------------------------------------
    def _build_scenes(self, pages: list[DocumentPage], drafts: dict[int, PageNarration]) -> None:
        scenes: list[Scene] = []
        for order, page in enumerate(pages, start=1):
            draft = drafts.get(page.index)
            if draft is None:
                continue
            valid_ids = {e.id for e in page.elements}
            segments: list[NarrationSegment] = []
            for seq, seg in enumerate(draft.segments, start=1):
                text = seg.text.strip()
                if not text:
                    continue
                segments.append(
                    NarrationSegment(
                        id=f"{scene_id(order)}_s{seq:02d}",
                        text=text,
                        # Drop refs the model invented; a wrong id would aim the camera at nothing.
                        element_refs=[ref for ref in seg.element_refs if ref in valid_ids],
                        emphasis=seg.emphasis,
                    )
                )
            if not segments:
                segments = self._split_into_segments(draft.narration, scene_id(order))

            narration = draft.narration.strip() or "".join(s.text for s in segments)
            scenes.append(
                Scene(
                    scene_id=scene_id(order),
                    source_page=page.index,
                    title=page.title,
                    narration=narration,
                    segments=segments,
                    duration=estimate_duration(narration, self.ctx.settings.tts_speech_rate),
                    visual=SceneVisual(
                        type=VisualType.SLIDE,
                        asset=page.image_path,
                        source_page=page.index,
                    ),
                )
            )
        self.project.scenes = scenes

    @staticmethod
    def _split_into_segments(text: str, prefix: str) -> list[NarrationSegment]:
        parts = [p.strip() for p in SENTENCE_SPLIT.split(text) if p.strip()]
        return [
            NarrationSegment(id=f"{prefix}_s{i:02d}", text=part)
            for i, part in enumerate(parts, start=1)
        ]
