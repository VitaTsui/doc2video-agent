"""presentation-narration — turn a script into timed scenes.

This service does not write the script; whoever calls it does. What lives here
is the part that has to be arithmetic rather than judgement:

* **The duration budget.** Each page gets a share of the target length, weighted
  by page type and emphasis, and that share becomes a character budget the
  caller writes to. Deciding length *after* TTS would mean re-voicing everything,
  so the budget has to exist before a word is written — which is why it is
  published to the caller (``narration_guide``) rather than kept internal.
* **Applying what comes back.** Splitting a page's script into sentence-level
  segments, binding those to page elements, and estimating each scene's duration.

``apply`` takes the caller's text; ``run`` falls back to a placeholder script so
the pipeline stays runnable end to end without one (useful for testing the
render path, not for a video anyone would watch).
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from ..core import telemetry
from ..core.ids import scene_id
from ..schemas import DocumentPage, NarrationSegment, PageType, Scene, SceneVisual, VisualType
from ..tools.tts import estimate_duration
from .base import Skill

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
        """Build scenes from a placeholder script — the no-caller-input path."""
        pages = self._pages()
        if not pages:
            return
        telemetry.record_degradation(
            "讲稿", "调用方未提供讲稿，使用占位文本；成片可渲染但内容无意义"
        )
        self._build_scenes(pages, self._write_heuristically(pages, self._allocate_budget(pages)))
        self.log.warning("使用占位讲稿：%d 个场景", len(self.project.scenes))

    def apply(self, narrations: dict[int, str]) -> list[int]:
        """Adopt caller-written narration, keyed by page index.

        Returns the pages that had no text supplied — they fall back to the
        placeholder so a partial script still renders, and the caller can see
        exactly which pages it missed rather than discovering it in the video.
        """
        pages = self._pages()
        if not pages:
            return []

        budgets = self._allocate_budget(pages)
        drafts: dict[int, PageNarration] = {}
        missing: list[int] = []
        for page in pages:
            text = (narrations.get(page.index) or "").strip()
            if text:
                drafts[page.index] = PageNarration(
                    index=page.index, narration=text, segments=[]
                )
            else:
                missing.append(page.index)
                drafts.update(self._write_heuristically([page], budgets))

        if missing:
            telemetry.record_degradation("讲稿", f"第 {missing} 页没有讲稿，使用占位文本")
            self.log.warning("这些页面没有讲稿，已用占位文本：%s", missing)

        self._build_scenes(pages, drafts)
        self.log.info(
            "讲稿已应用：%d 个场景，预计 %.1f 秒",
            len(self.project.scenes),
            self.project.total_duration(),
        )
        return missing

    def guide(self) -> list[dict]:
        """Per-page writing budget, for the caller to write against.

        Without this the caller is guessing at length, and a script that misses
        the requested duration cannot be fixed after the audio exists.
        """
        pages = self._pages()
        budgets = self._allocate_budget(pages)
        return [
            {
                "page": page.index,
                "title": page.title,
                "page_type": page.page_type.value,
                "target_seconds": round(budgets[page.index], 1),
                "target_chars": self._char_budget(budgets[page.index]),
            }
            for page in pages
        ]

    def _pages(self) -> list[DocumentPage]:
        skip = set(self.project.intent.skip_pages)
        pages = [p for p in self.project.document.ordered_pages() if p.index not in skip]
        if not pages:
            self.log.warning("没有可讲解的页面")
        return pages

    def rewrite_scene(self, scene: Scene, narration: str) -> None:
        """Replace one scene's script — the incremental-edit entry point.

        Only this scene changes, which is what keeps a "page 7 is too long" edit
        from re-voicing and re-rendering the whole video (方案 §9).
        """
        text = narration.strip()
        if not text:
            self.log.warning("场景 %s 的新讲稿为空，跳过", scene.scene_id)
            return
        scene.narration = text
        scene.segments = self._split_into_segments(text, scene.scene_id)
        scene.duration = estimate_duration(text, self.ctx.settings.tts_speech_rate)

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
