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

from ..core import ledger, telemetry, tuning
from ..core.ids import scene_id
from ..schemas import DocumentPage, NarrationSegment, PageType, Scene, SceneVisual, VisualType
from ..tools.llm import model_schema
from ..tools.tts import estimate_duration
from .base import ProgressFn, Skill, load_prompt

# Pages per model call. Small enough that a long deck cannot overrun the
# output budget, large enough that each page can see its neighbours.
BATCH_SIZE = 4
# How far past the requested length a script may run before it is cut back.
# Some slack on purpose: trimming a page costs it a sentence, and a video ten
# percent long is not worth a sentence.
DURATION_TOLERANCE = 0.10

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


# What a style name means to someone writing the words. The enum value alone
# ("lively") tells the model a category; these tell it what to do differently,
# which is the only form a style instruction can be obeyed in.
STYLE_BRIEF = {
    "professional": "专业、克制。用行业里通行的说法，不解释常识，不铺陈形容词。",
    "tech": "技术向。讲清楚机制与取舍，敢用具体数字和术语，句子干脆。",
    "lively": "活泼。多用短句和口语连接，可以带一点反问和轻微夸张，不端着。",
    "casual": "轻松。像同事之间讲一件事，允许口语词和插入语，不用书面套话。",
    "formal": "正式。完整句、书面用词，不用口语缩略，语气持重。",
}


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

    def run(
        self, written: dict[int, str] | None = None, *, progress: ProgressFn | None = None
    ) -> None:
        """Write the script ourselves — the path taken when nobody supplied one.

        With a model configured this is a real script. Without one it is
        placeholder text: renderable, correctly timed, and meaningless — which
        is why it is recorded as a degradation rather than passed off as a
        result. The caller-written path (``apply``) remains the primary one.

        ``written`` is the half-written case, which is the common one: someone
        has typed the pages they care about and wants the rest filled in. Those
        pages are not rewritten, not trimmed, and not shown to the model as
        something to improve — they are shown as context, because a page whose
        neighbours are already written has to continue from them rather than
        open on its own.
        """
        pages = self._pages()
        if not pages:
            return
        budgets = self._allocate_budget(pages)
        kept = self._kept(pages, written)
        if len(kept) == len(pages):
            # Nothing to write. Still worth running: the text has to be split
            # into segments and timed before anything downstream can use it.
            drafts = {index: _as_draft(index, text) for index, text in kept.items()}
        else:
            drafts = self.try_llm(
                lambda: self._write_with_model(pages, budgets, kept, progress=progress),
                lambda: self._placeholder(pages, budgets, kept),
                what="讲稿",
            )
        drafts = self._fit_duration(pages, budgets, drafts, frozen=set(kept))
        self._build_scenes(pages, drafts)
        self.log.info(
            "讲稿完成：%d 个场景（其中 %d 页是人写的）", len(self.project.scenes), len(kept)
        )

    @staticmethod
    def _kept(pages: list[DocumentPage], written: dict[int, str] | None) -> dict[int, str]:
        """The pages someone has actually written, in page order."""
        indexes = {page.index for page in pages}
        return {
            index: text.strip()
            for index, text in sorted((written or {}).items())
            if index in indexes and text and text.strip()
        }

    # -- fitting --------------------------------------------------------
    def _fit_duration(
        self,
        pages: list[DocumentPage],
        budgets: dict[int, float],
        drafts: dict[int, PageNarration],
        frozen: set[int] | None = None,
    ) -> dict[int, PageNarration]:
        """Bring the script back to the length that was asked for.

        The budget exists before a word is written and the model overruns it
        anyway — on a real 30-page deck it wrote 38% past, which came out as a
        video 160 seconds longer than the one that was ordered. Review reported
        that and nothing acted on it, so "做成八分钟" quietly meant eleven.

        Not by speeding the voice up: that is the thing a listener complains
        about, and it makes a long script into a rushed one rather than a
        shorter one. Pages are compressed instead, worst overrun first, and the
        ones the user marked as important are compressed last.

        ``frozen`` pages are never cut. Someone wrote those words on purpose,
        and silently deleting a sentence out of them would be the worst thing
        this function could do — the overrun is the model's to pay for.
        """
        target = float(self.project.intent.duration)
        if target <= 0 or not drafts:
            return drafts

        silence = self._page_silence() * len(pages)
        pace = self._pace()

        def spoken(text: str) -> float:
            return len(text) / pace

        total = sum(spoken(d.narration) for d in drafts.values()) + silence
        if total <= target * (1 + DURATION_TOLERANCE):
            return drafts

        # Whose fault it is, most-to-least: how far past its own budget a page
        # ran, discounted by how much the user said it matters.
        emphasis = set(self.project.intent.emphasis_pages)
        frozen = frozen or set()
        over = []
        for page in pages:
            draft = drafts.get(page.index)
            if draft is None or page.index in frozen:
                continue
            allowed = budgets.get(page.index, 0.0)
            excess = spoken(draft.narration) - allowed
            if excess <= 0:
                continue
            over.append((excess * (0.4 if page.index in emphasis else 1.0), page.index, allowed))
        over.sort(reverse=True)

        self.log.info(
            "讲稿比目标长 %.0f 秒（%.0fs / 目标 %.0fs），压 %d 页",
            total - target,
            total,
            target,
            len(over),
        )
        telemetry.record_degradation(
            "讲稿", f"超出目标时长 {total - target:.0f} 秒，压缩 {len(over)} 页"
        )

        trimmed = dict(drafts)
        for _, index, allowed in over:
            if total <= target * (1 + DURATION_TOLERANCE):
                break
            draft = trimmed[index]
            shorter = _trim_to(draft.narration, int(allowed * pace))
            if len(shorter) >= len(draft.narration):
                continue
            total -= spoken(draft.narration) - spoken(shorter)
            trimmed[index] = PageNarration(index=index, narration=shorter, segments=[])
        return trimmed

    def _placeholder(
        self,
        pages: list[DocumentPage],
        budgets: dict[int, float],
        kept: dict[int, str] | None = None,
    ) -> dict[int, PageNarration]:
        # No degradation recorded here: try_llm already logged one, with the
        # reason attached. A second record for the same event would double the
        # count that cross-run metrics compare.
        kept = kept or {}
        blanks = [page for page in pages if page.index not in kept]
        self.log.warning("使用占位讲稿（成片可渲染但内容无意义）：%d 页", len(blanks))
        drafts = {index: _as_draft(index, text) for index, text in kept.items()}
        drafts.update(self._write_heuristically(blanks, budgets))
        return drafts

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

        ``target_seconds`` is time *spoken*; ``page_seconds`` is how long the
        page is on screen. They differ by the silence held at each end of a
        page, which is part of the video but not of the script.
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
                "page_seconds": round(budgets[page.index] + self._page_silence(), 1),
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
        scene.duration = estimate_duration(
            text, self.ctx.settings.tts_speech_rate, self._pace()
        )

    def revise_scene(self, scene: Scene, instruction: str, target_seconds: float = 0.0) -> None:
        """Rewrite one scene from an instruction rather than from new text.

        This is what "第 3 页太长了，压到 20 秒" has to go through. The
        instruction is not narration — turning it into narration needs a model,
        and without one the honest outcome is to leave the scene alone and say
        so. Silently re-voicing the same words would report success for a run
        that changed nothing.
        """
        target = target_seconds or scene.duration
        budget = self._char_budget(max(target - self._page_silence(), MIN_SCENE_SECONDS))
        page = self.project.document.page(scene.source_page)

        def fallback() -> None:
            self.log.warning("没有模型，无法按「%s」改写场景 %s", instruction, scene.scene_id)

        def rewrite() -> None:
            with ledger.call(
                self.llm.source,
                f"改写 {scene.scene_id}",
                covers=[ledger.scene_key(scene.scene_id)],
            ):
                result = self.llm.complete_json(
                    self._revise_prompt(scene, page, instruction, budget),
                    schema=model_schema(PageNarration),
                    system=load_prompt("narration"),
                )
            revised = PageNarration.model_validate(result)
            if revised.narration.strip():
                self.rewrite_scene(scene, revised.narration.strip())

        self.try_llm(rewrite, fallback, what=f"修改第 {scene.source_page} 页")

    def _revise_prompt(
        self, scene: Scene, page: DocumentPage | None, instruction: str, budget: int
    ) -> str:
        lines = [
            f"# 修改第 {scene.source_page} 页的讲稿",
            f"用户的要求：{instruction}",
            f"改完后这一页的讲稿控制在 {budget} 字左右（正负 15%）。",
            "",
            "# 现在的讲稿",
            scene.narration,
        ]
        if page is not None:
            lines += ["", f"# 这一页的内容（标题：{page.title or '无'}）"]
            if page.key_points:
                lines.append("关键点：" + "；".join(page.key_points))
            lines.extend(
                f"  - {e.id}｜{e.kind.value}｜{_truncate(e.text, 120)}"
                for e in page.elements
                if e.text
            )
        lines += ["", f"只返回这一页，index 用 {scene.source_page}。"]
        return "\n".join(lines)

    def _page_silence(self) -> float:
        """Seconds each page holds without speech, at its head and its tail."""
        return tuning.value("voice.lead", self.ctx.settings) + tuning.value(
            "voice.tail", self.ctx.settings
        )

    def _allocate_budget(self, pages: list[DocumentPage]) -> dict[int, float]:
        """Seconds of *speech* per page, summing to the requested duration.

        The silence around each page is a fixed cost that has to come off the
        top: it is on screen but nobody is talking, so budgeting it as writing
        time would make every page's script overrun by that much — a 16-page
        deck would run 24 seconds long against its target.
        """
        intent = self.project.intent
        silence = self._page_silence() * len(pages)
        # Never let the reservation eat the whole request: a very short target
        # on a long deck should still produce a script, just a terse one.
        speech_total = max(intent.duration - silence, intent.duration * 0.4)
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
            seconds = speech_total * weight / total_weight
            budgets[index] = max(MIN_SCENE_SECONDS, min(MAX_SCENE_SECONDS, seconds))
        return budgets

    def _char_budget(self, seconds: float) -> int:
        """How many characters fit in `seconds`, for the engine that will speak them.

        The number used to be written here as well as in the estimator, and the
        two were the same until they were not: a page budgeted against 4.6
        characters a second and spoken at 4.15 runs eleven percent long before
        the model has done anything wrong. One source now, and it follows the
        voice — `say` says 4.75, Edge's broadcast voice 4.15.
        """
        return int(seconds * self._pace())

    def _pace(self) -> float:
        from ..tools.tts import TTSTool

        return TTSTool(self.ctx.settings).chars_per_second

    # -- model path -----------------------------------------------------
    def _write_with_model(
        self,
        pages: list[DocumentPage],
        budgets: dict[int, float],
        kept: dict[int, str] | None = None,
        *,
        progress: ProgressFn | None = None,
    ) -> dict[int, PageNarration]:
        """Write the whole deck in batches.

        Batched rather than per-page because a script's job is to connect: a
        page written without sight of its neighbours opens with a transition
        from nothing. Batched rather than all-at-once because a long deck
        overruns the output budget, and a truncated reply loses whole pages.

        Pages in ``kept`` are already written. They stay in their batch — that
        is how the model sees what the page before and after actually says —
        but they are asked for back. A batch where every page is written is
        skipped entirely, which is what makes "fill in the three I left blank"
        cost three pages of writing rather than thirty.

        Whatever the model does not return is filled in heuristically. A model
        that skips page 7 must not cost page 7 its narration.
        """
        kept = kept or {}
        drafts: dict[int, PageNarration] = {
            index: _as_draft(index, text) for index, text in kept.items()
        }
        for start in range(0, len(pages), BATCH_SIZE):
            batch = pages[start : start + BATCH_SIZE]
            if all(page.index in kept for page in batch):
                continue
            # Said before the call, not after. A batch is one model call and
            # takes a minute or more; a count that only moves when a call
            # returns sits still for that whole minute, and the window has no
            # way to say which pages are being written *now* — which is the
            # one thing the person waiting wants to know.
            if progress is not None:
                progress(
                    "narrate",
                    f"第 {batch[0].index}-{batch[-1].index} 页",
                    len(drafts),
                    len(pages),
                )
            with ledger.call(
                self.llm.source,
                f"第 {batch[0].index}-{batch[-1].index} 页",
                covers=[ledger.page_key(page.index) for page in batch if page.index not in kept],
            ):
                result = self.llm.complete_json(
                    self._prompt(batch, budgets, position=start, kept=kept, pages=pages),
                    schema=model_schema(NarrationResult),
                    system=load_prompt("narration"),
                    max_tokens=self.ctx.settings.llm_max_tokens,
                )
            for page in NarrationResult.model_validate(result).pages:
                # A model that rewrites a page it was told to leave alone gets
                # ignored rather than obeyed.
                if page.narration.strip() and page.index not in kept:
                    drafts[page.index] = page
            # Save what has been written so far. A deck takes several calls to
            # write and each one is a wait; a reader watching the pages fill in
            # is being told the truth about where it has got to, where a blank
            # list until the last batch lands is not.
            self._build_scenes(pages, drafts)
            self.ctx.store.save(self.project)

        wanted = {p.index for p in pages}
        missing = sorted(wanted - drafts.keys())
        if missing:
            telemetry.record_degradation("讲稿", f"模型漏掉第 {missing} 页，改用占位文本")
            self.log.warning("模型未覆盖 %s，这几页使用占位讲稿", missing)
            drafts.update(
                self._write_heuristically([p for p in pages if p.index in set(missing)], budgets)
            )
        return {index: drafts[index] for index in sorted(wanted)}

    def _prompt(
        self,
        batch: list[DocumentPage],
        budgets: dict[int, float],
        *,
        position: int,
        kept: dict[int, str] | None = None,
        pages: list[DocumentPage] | None = None,
    ) -> str:
        document = self.project.document
        intent = self.project.intent
        lines = [
            f"# 文档：{document.title or '未命名'}",
            f"主题：{document.topic or '未知'}",
            f"整体摘要：{document.summary or '（无）'}",
            "",
            f"# 观众与风格\n受众：{intent.audience}\n"
            f"风格：{STYLE_BRIEF.get(intent.style, intent.style)}\n"
            f"语气：{intent.tone}\n"
            f"额外要求：{intent.instructions or '（无）'}",
            "",
        ]
        kept = kept or {}
        if kept:
            lines += [
                "# 这次的任务：补齐没写的页面",
                "下面标了「已写好」的页面是人自己写的。原样保留，不要改写、不要润色，"
                "也不要在结果里输出它们。你要写的是标了「待补写」的页面，"
                "并且要跟前后已写好的内容接得上：不重复它们讲过的话，"
                "承接它们的说法和称呼，转场自然。",
                "",
            ]
            if before := self._nearest_written(pages, kept, batch[0].index, back=True):
                lines += [f"# 上文（第 {before[0]} 页，已写好，结尾）", _tail(before[1], 200), ""]
            if after := self._nearest_written(pages, kept, batch[-1].index, back=False):
                lines += [f"# 下文（第 {after[0]} 页，已写好，开头）", _truncate(after[1], 200), ""]
        lines.append(f"# 页面（全文第 {position + 1} 页起，共 {len(budgets)} 页）")
        for page in batch:
            mark = "已写好" if page.index in kept else ("待补写" if kept else "")
            lines.append(
                f"\n## 第 {page.index} 页｜{page.page_type.value}｜{page.title or '无标题'}"
                + (f"｜{mark}" if mark else "")
            )
            if text := kept.get(page.index):
                lines.append(f"已写好的讲稿：{text}")
                continue
            if flow := _flow_of(page):
                # The order the arrows go, which is the order the page has to
                # be explained in. Given to the writer rather than used to
                # reorder the camera: a shot is bound to the sentence that
                # mentions it, and pointing at a box the current sentence is
                # not talking about is worse than crossing the diagram out of
                # order (方案 §20).
                lines.append(f"这一页画的是一条流程，按箭头走是：{flow}")
            lines.append(f"字数预算：{self._char_budget(budgets[page.index])} 字（正负 15%）")
            if page.summary:
                lines.append(f"页面摘要：{page.summary}")
            if page.key_points:
                lines.append("关键点：" + "；".join(page.key_points))
            if page.speaker_notes:
                lines.append(f"演讲者备注：{_truncate(page.speaker_notes, 300)}")
            elements = [e for e in page.elements if e.text]
            if elements:
                lines.append("元素：")
                lines.extend(
                    f"  - {e.id}｜{e.kind.value}｜{_truncate(e.text, 120)}" for e in elements
                )
        return "\n".join(lines)

    @staticmethod
    def _nearest_written(
        pages: list[DocumentPage] | None,
        kept: dict[int, str],
        edge: int,
        *,
        back: bool,
    ) -> tuple[int, str] | None:
        """The written page just outside this batch, on one side.

        The batch itself carries most of the continuity. This carries the rest:
        the page before the batch and the page after it are what the first and
        last pages of the batch have to join onto, and they are usually in a
        different call.
        """
        order = [page.index for page in (pages or [])]
        candidates = [i for i in order if (i < edge if back else i > edge) and i in kept]
        if not candidates:
            return None
        index = max(candidates) if back else min(candidates)
        return index, kept[index]

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
                    duration=estimate_duration(
                        narration, self.ctx.settings.tts_speech_rate, self._pace()
                    ),
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


def _as_draft(index: int, text: str) -> PageNarration:
    """Hand-written text as a draft. Segments are cut later, by the same
    splitter the model's own text goes through — there is no reason for a page
    someone typed to be segmented differently from one that was generated."""
    return PageNarration(index=index, narration=text, segments=[])


def _tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else "…" + text[-limit:]


def _truncate(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _flow_of(page: DocumentPage) -> str:
    """A page's declared flow as words, or empty when it declares none."""
    if page.diagram is None:
        return ""
    labels = {e.id: (e.text or e.label or e.id).strip() for e in page.elements}
    walked = [labels.get(node, node) for node in page.diagram.order()]
    return " → ".join(name for name in walked if name)


def _trim_to(text: str, limit: int) -> str:
    """Drop whole sentences from the end until the text fits.

    Sentences rather than characters: cutting mid-sentence leaves the narrator
    stopping in the middle of a thought, which is worse than the page being
    long. The first sentence is always kept — a page that says nothing is not
    a shorter page, it is a missing one.
    """
    if len(text) <= limit:
        return text
    parts = [part for part in re.split(r"(?<=[。！？])", text) if part.strip()]
    if len(parts) <= 1:
        return text
    kept = [parts[0]]
    for part in parts[1:]:
        if len("".join(kept)) + len(part) > limit:
            break
        kept.append(part)
    return "".join(kept)
