"""presentation-understanding — what is this deck about, and in what order.

Turns the parser's structural output into a Document Model with page types,
summaries, key points, narrative sections and an explaining order.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..schemas import DocumentPage, ElementKind, PageType, Section
from ..tools.llm import model_schema
from .base import Skill, load_prompt

# Pages per LLM call: large enough for cross-page context, small enough that one
# bad page does not cost the whole deck.
BATCH_SIZE = 6

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

        self.try_llm(self._understand_with_llm, self._understand_heuristically, what="文档理解")

        if not document.presentation_order:
            document.presentation_order = [
                p.index for p in document.pages if p.page_type is not PageType.CONTACT
            ]
        self.log.info(
            "文档理解完成：%d 页，%d 个章节", len(document.pages), len(document.sections)
        )

    # -- LLM path ------------------------------------------------------
    def _understand_with_llm(self) -> None:
        document = self.project.document
        pages = sorted(document.pages, key=lambda p: p.index)
        schema = model_schema(DeckUnderstanding)
        system = load_prompt("document_understanding")

        merged_pages: dict[int, PageUnderstanding] = {}
        deck_level: DeckUnderstanding | None = None

        for start in range(0, len(pages), BATCH_SIZE):
            batch = pages[start : start + BATCH_SIZE]
            prompt = self._render_prompt(batch, len(pages))
            raw = self.llm.complete_json(prompt, schema=schema, system=system)
            result = DeckUnderstanding.model_validate(raw)
            # Deck-level fields come from the first batch, which sees the cover
            # and agenda — the pages that actually describe the whole document.
            deck_level = deck_level or result
            for page_result in result.pages:
                merged_pages[page_result.index] = page_result

        if deck_level is not None:
            document.topic = deck_level.topic or document.topic
            document.summary = deck_level.summary or document.summary
            document.key_concepts = deck_level.key_concepts
            document.sections = deck_level.sections
            order = [i for i in deck_level.presentation_order if document.page(i)]
            document.presentation_order = order

        for page in pages:
            understanding = merged_pages.get(page.index)
            if understanding is None:
                self._apply_heuristics(page, len(pages))
                continue
            page.page_type = understanding.page_type
            page.title = understanding.title or page.title
            page.summary = understanding.summary
            page.key_points = understanding.key_points
            scores = {e.id: e.importance for e in understanding.elements}
            for element in page.elements:
                if element.id in scores:
                    element.importance = max(0.0, min(1.0, scores[element.id]))

    def _render_prompt(self, batch: list[DocumentPage], total_pages: int) -> str:
        lines = [
            f"文档标题：{self.project.document.title or '（未知）'}",
            f"总页数：{total_pages}",
            "",
            "以下是本批次待分析的页面：",
        ]
        for page in batch:
            lines.append(f"\n## 第 {page.index} 页")
            if page.title:
                lines.append(f"解析出的标题：{page.title}")
            if page.speaker_notes:
                lines.append(f"演讲者备注：{page.speaker_notes}")
            lines.append("元素：")
            for element in page.elements:
                text = element.text.replace("\n", " ")[:160]
                lines.append(f"- [{element.id}] ({element.kind}) {text}")
        lines.append(
            "\n请对以上每一页给出分析结果，并给出整份文档的主题、摘要、关键概念、章节划分与讲解顺序。"
        )
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
