"""presentation-understanding — what is this deck about, and in what order.

Turns the parser's structural output into a Document Model with page types,
summaries, key points, narrative sections and an explaining order.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..core import ledger, telemetry
from ..schemas import DocumentPage, ElementKind, PageType, Section
from ..tools.llm import model_schema
from .base import Skill, load_prompt

# Pages per LLM call: large enough for cross-page context, small enough that one
# bad page does not cost the whole deck.
BATCH_SIZE = 6
# Attaching page renders is what lets the model read a diagram or a chart, but
# images are expensive — send them only for the batches that need them, and cap
# how many ride along in one request.
MAX_IMAGES_PER_BATCH = 4
# Below this much text a page is carrying its meaning visually, not in words.
TEXT_LIGHT_THRESHOLD = 80

COVER_HINTS = ("公司", "介绍", "简介", "product", "introduction", "overview")
AGENDA_HINTS = ("目录", "议程", "contents", "agenda", "outline")
SUMMARY_HINTS = ("总结", "小结", "结论", "summary", "conclusion", "thank")
CONTACT_HINTS = ("联系", "contact", "微信", "电话", "email", "邮箱")
ARCH_HINTS = ("架构", "architecture", "技术方案", "系统", "流程")


class ElementScore(BaseModel):
    id: str
    importance: float = Field(description="0..1")


class PageUnderstanding(BaseModel):
    index: int
    page_type: PageType
    title: str
    summary: str
    key_points: list[str]
    elements: list[ElementScore]


class DeckUnderstanding(BaseModel):
    topic: str
    summary: str
    key_concepts: list[str]
    sections: list[Section]
    presentation_order: list[int]
    pages: list[PageUnderstanding]


# Clause delimiters — cutting here keeps a number or a term intact.
_BOUNDARIES = "。；;，,、！？!?)）】」"


def _truncate(text: str, limit: int) -> str:
    """Trim to ``limit`` without slicing through a number or a word.

    Chart summaries carry figures like "3,100", where the comma is a thousands
    separator rather than a clause break — cutting there leaves "3," in the
    script, which is worse than dropping the clause entirely.
    """
    if len(text) <= limit:
        return text

    for index in range(limit - 1, limit // 2 - 1, -1):
        if text[index] in _BOUNDARIES and not _inside_number(text, index):
            return text[: index + 1]

    space = text.rfind(" ", limit // 2, limit)
    return text[:space] if space > 0 else text[:limit]


def _inside_number(text: str, index: int) -> bool:
    """True for a separator sitting between two digits, e.g. the comma in 3,100."""
    if text[index] not in ",，.":
        return False
    before = text[index - 1] if index > 0 else ""
    after = text[index + 1] if index + 1 < len(text) else ""
    return before.isdigit() and after.isdigit()


class DocumentSkill(Skill):
    name = "presentation-understanding"
    description = "理解整份演示文档的结构、重点和叙事逻辑"

    def run(self) -> None:
        document = self.project.document
        if not document.pages:
            self.log.warning("文档没有可分析的页面")
            return

        # The heuristics run first either way: they fill every field with
        # something defensible, so a model that answers for six pages out of
        # twenty leaves the other fourteen classified rather than blank.
        self._understand_heuristically()
        self.try_llm(
            self._understand_with_model,
            lambda: None,
            what="文档理解",
        )

        if not document.presentation_order:
            document.presentation_order = [
                p.index for p in document.pages if p.page_type is not PageType.CONTACT
            ]
        self.log.info(
            "文档理解完成：%d 页，%d 个章节", len(document.pages), len(document.sections)
        )

    # -- model path ----------------------------------------------------
    def _understand_with_model(self) -> None:
        """Overwrite the heuristics with what the model read, page by page.

        Page renders ride along for the batches that need them: a chart or an
        architecture diagram carries its meaning in the picture, and its
        extracted text is a list of axis labels. Text-heavy pages send no image
        — it would cost tokens to tell the model what it already read.

        Applied field by field rather than by replacing the page, so anything
        the model omits keeps its heuristic value instead of being blanked.
        """
        document = self.project.document
        pages = document.pages
        by_index = {p.index: p for p in pages}

        failures = 0
        for start in range(0, len(pages), BATCH_SIZE):
            batch = pages[start : start + BATCH_SIZE]
            # One batch failing must not cost the others. It used to: the
            # exception left the loop, and a single page whose title contained
            # a quotation mark took the remaining batches with it — those
            # pages fell back to heuristics for no reason of their own.
            where = f"第 {batch[0].index}-{batch[-1].index} 页"
            try:
                with ledger.call(
                    self.llm.source,
                    where,
                    covers=[ledger.page_key(p.index) for p in batch],
                ):
                    result = DeckUnderstanding.model_validate(
                        self.llm.complete_json(
                            self._prompt(batch),
                            schema=model_schema(DeckUnderstanding),
                            system=load_prompt("document_understanding"),
                            images=self._images_for(batch),
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - one batch, not the deck
                failures += 1
                detail = f"{exc}"
                self.log.warning("%s的理解失败，这几页改用启发式规则：%s", where, detail)
                telemetry.record_degradation("文档理解", f"{where}：{detail}"[:300])
                ledger.degradation("文档理解降级", f"{where} 改用启发式规则：{detail}"[:300])
                continue
            for read in result.pages:
                page = by_index.get(read.index)
                if page is not None:
                    self._apply_understanding(page, read)

            # Deck-level fields come back with every batch; the first batch —
            # which holds the cover — is the one that knows what the deck is.
            if start == 0:
                seen = {page.index for page in batch}
                document.topic = result.topic or document.topic
                document.summary = result.summary or document.summary
                document.key_concepts = result.key_concepts or document.key_concepts
                if result.sections:
                    document.sections = result.sections
                if result.presentation_order:
                    known = {p.index for p in pages}
                    ordered = [i for i in result.presentation_order if i in known]
                    # The ordering is the one deck-level field a batch cannot
                    # answer: this one has seen six pages of thirty, so every
                    # page it never read is missing from its answer — not
                    # dropped on purpose. A 30-page deck became a 6-page video
                    # this way, silently, because `ordered_pages()` obeys.
                    #
                    # So a page may only be left out by a batch that saw it.
                    document.presentation_order = ordered + [
                        page.index
                        for page in pages
                        if page.index not in set(ordered)
                        and page.index not in seen
                        and page.page_type is not PageType.CONTACT
                    ]

        if failures and failures * BATCH_SIZE >= len(pages):
            # Nothing came back at all — say so as one degradation about the
            # stage rather than as a list of per-batch ones nobody reads.
            raise RuntimeError(f"{failures} 批全部失败，文档理解没有任何模型结果")

    @staticmethod
    def _apply_understanding(page: DocumentPage, read: PageUnderstanding) -> None:
        page.page_type = read.page_type
        page.title = read.title or page.title
        page.summary = read.summary or page.summary
        page.key_points = read.key_points or page.key_points
        # Element ids are matched, never trusted: a score for an id the model
        # invented would aim the camera at nothing.
        by_id = {e.id: e for e in page.elements}
        for score in read.elements:
            element = by_id.get(score.id)
            if element is not None:
                element.importance = max(0.0, min(1.0, score.importance))

    def _images_for(self, batch: list[DocumentPage]) -> list:
        """Page renders for the most visual pages in this batch."""
        if not self.llm.supports_images():
            return []
        ranked = sorted(batch, key=_visual_weight, reverse=True)
        paths = []
        for page in ranked[:MAX_IMAGES_PER_BATCH]:
            path = self.ctx.asset_path(page.image_path)
            if path is not None and path.exists():
                paths.append(path)
        return paths

    def _prompt(self, batch: list[DocumentPage]) -> str:
        document = self.project.document
        lines = [
            f"# 文档：{document.title or '未命名'}（共 {len(document.pages)} 页）",
            "",
            "# 本批页面",
        ]
        for page in batch:
            lines.append(f"\n## 第 {page.index} 页｜解析出的标题：{page.title or '（空）'}")
            if page.speaker_notes:
                lines.append(f"演讲者备注：{_truncate(page.speaker_notes, 300)}")
            elements = [e for e in page.elements if e.text]
            if elements:
                lines.append("元素：")
                lines.extend(
                    f"  - {e.id}｜{e.kind.value}｜{_truncate(e.text, 150)}" for e in elements
                )
            else:
                lines.append("（这一页没有可提取的文字，请看配图）")
        return "\n".join(lines)

    # -- heuristic path ------------------------------------------------
    def _understand_heuristically(self) -> None:
        document = self.project.document
        total = len(document.pages)
        for page in document.pages:
            self._apply_heuristics(page, total)

        # The cover's title describes the deck; the filename usually does not.
        cover_title = next((p.title for p in document.pages if p.title), "")
        document.topic = document.topic or cover_title or document.title
        document.summary = document.summary or "、".join(
            p.title for p in document.pages[:3] if p.title
        )
        document.key_concepts = [p.title for p in document.pages if p.title][:8]
        document.sections = self._build_sections(document.pages)
        document.presentation_order = [
            p.index for p in document.pages if p.page_type is not PageType.CONTACT
        ]

    def _apply_heuristics(self, page: DocumentPage, total_pages: int) -> None:
        page.page_type = self._guess_page_type(page, total_pages)
        page.key_points = page.key_points or self._extract_key_points(page)
        if not page.summary:
            head = page.title or (page.key_points[0] if page.key_points else "")
            page.summary = head[:80]

    def _guess_page_type(self, page: DocumentPage, total_pages: int) -> PageType:
        haystack = f"{page.title} {page.raw_text()[:200]}".lower()

        def has(hints: tuple[str, ...]) -> bool:
            return any(hint in haystack for hint in hints)

        if page.index == 1:
            return PageType.COVER
        if has(AGENDA_HINTS):
            return PageType.AGENDA
        if has(CONTACT_HINTS):
            return PageType.CONTACT
        if has(SUMMARY_HINTS) or page.index == total_pages:
            return PageType.SUMMARY
        if has(ARCH_HINTS):
            return PageType.ARCHITECTURE
        if any(e.kind is ElementKind.CHART for e in page.elements):
            return PageType.CHART
        if any(e.kind is ElementKind.IMAGE for e in page.elements) and len(page.elements) <= 3:
            return PageType.SECTION
        return PageType.CONTENT

    def _extract_key_points(self, page: DocumentPage) -> list[str]:
        points: list[str] = []
        for element in page.elements:
            if element.kind in (ElementKind.BULLET, ElementKind.NUMBER, ElementKind.CHART):
                for line in element.text.split("\n"):
                    cleaned = line.strip(" •-·*\t")
                    if len(cleaned) >= 2:
                        points.append(_truncate(cleaned, 60))
        if not points:
            body = [
                e.text.strip()
                for e in page.elements
                if e.kind in (ElementKind.PARAGRAPH, ElementKind.SUBTITLE) and e.text.strip()
            ]
            points = [_truncate(t, 60) for t in body]
        return points[:4]

    def _build_sections(self, pages: list[DocumentPage]) -> list[Section]:
        """Group pages into sections at every section/agenda page boundary."""
        sections: list[Section] = []
        current: Section | None = None
        for page in pages:
            starts_section = page.page_type in (PageType.SECTION, PageType.AGENDA, PageType.COVER)
            if current is None or starts_section:
                current = Section(title=page.title or f"第 {page.index} 部分", page_indexes=[])
                sections.append(current)
            current.page_indexes.append(page.index)
        return sections


def _visual_weight(page: DocumentPage) -> float:
    """How much of this page's meaning is only available in the picture."""
    weight = 0.0
    for element in page.elements:
        if element.kind is ElementKind.CHART:
            weight += 2.0
        elif element.kind is ElementKind.IMAGE:
            weight += 1.5
        elif element.kind is ElementKind.TABLE:
            weight += 0.5
    # A page with almost no text is either a diagram or a section break; both
    # are better judged from the render than from their few words.
    if len(page.raw_text()) < TEXT_LIGHT_THRESHOLD:
        weight += 1.0
    if page.page_type is PageType.ARCHITECTURE:
        weight += 1.5
    return weight
