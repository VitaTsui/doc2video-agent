"""Document Model — answers "what is this document about".

Layer 1 of the three intermediate models. Produced by the document skill from
raw parser output plus LLM/VLM understanding. Everything downstream (narration,
director) reads element ids and bounding boxes from here, so this is the only
place that knows about pages and pixels.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class BBox(BaseModel):
    """Axis-aligned box in *rendered page pixel* coordinates (origin top-left)."""

    x: float
    y: float
    w: float
    h: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2, self.y + self.h / 2)

    def normalized(self, page_w: float, page_h: float) -> BBox:
        """Return the same box in 0..1 space — what renderers actually consume."""
        return BBox(x=self.x / page_w, y=self.y / page_h, w=self.w / page_w, h=self.h / page_h)

    def padded(self, ratio: float = 0.08) -> BBox:
        """Grow the box so a zoom target does not sit flush against the frame edge."""
        dx, dy = self.w * ratio, self.h * ratio
        return BBox(x=self.x - dx, y=self.y - dy, w=self.w + 2 * dx, h=self.h + 2 * dy)


class ElementKind(StrEnum):
    TITLE = "title"
    SUBTITLE = "subtitle"
    BULLET = "bullet"
    PARAGRAPH = "paragraph"
    IMAGE = "image"
    CHART = "chart"
    TABLE = "table"
    NUMBER = "number"
    SHAPE = "shape"
    OTHER = "other"


class PageType(StrEnum):
    COVER = "cover"
    AGENDA = "agenda"
    SECTION = "section"
    CONTENT = "content"
    CHART = "chart"
    ARCHITECTURE = "architecture"
    COMPARISON = "comparison"
    CASE = "case"
    SUMMARY = "summary"
    CONTACT = "contact"
    OTHER = "other"


class ChartSeriesFacts(BaseModel):
    name: str = ""
    # None is a gap in the data, not a zero: a line must not be joined across it.
    values: list[float | None] = Field(default_factory=list)
    color: str = ""


class ChartFacts(BaseModel):
    """A chart's numbers, as the deck states them.

    Enough to draw the chart again and no more — the deck's own categories,
    series, values and colours. Re-palettizing or re-scaling would make the
    video disagree with the slide it came from, so neither is possible here.
    """

    kind: str = "column"
    title: str = ""
    categories: list[str] = Field(default_factory=list)
    series: list[ChartSeriesFacts] = Field(default_factory=list)


class DiagramEdge(BaseModel):
    """One arrow, by the elements it actually joins."""

    source: str
    target: str
    # What the connector had written on it, when anything did.
    label: str = ""


class DiagramFacts(BaseModel):
    """A flow on a slide, read rather than recognised.

    Present only when the deck *declares* the structure: a connector in OOXML
    names the shapes it starts and ends at, so the graph is a fact about the
    file rather than a guess about a picture. That distinction is the whole
    gate — a diagram whose arrows are drawn as free-floating lines cannot be
    read this way, and is left as the picture it is (方案 §12、§20).
    """

    nodes: list[str] = Field(default_factory=list)
    edges: list[DiagramEdge] = Field(default_factory=list)

    def order(self) -> list[str]:
        """The nodes in the order the arrows walk them.

        A flow is told in the order it flows: the narration and the camera
        should follow the arrows rather than the reading order of the boxes,
        which on a slide is often neither left-to-right nor top-to-bottom.
        Cycles keep their remaining nodes rather than dropping them.
        """
        incoming = {node: 0 for node in self.nodes}
        outgoing: dict[str, list[str]] = {node: [] for node in self.nodes}
        for edge in self.edges:
            if edge.source in incoming and edge.target in incoming:
                incoming[edge.target] += 1
                outgoing[edge.source].append(edge.target)

        ready = [node for node in self.nodes if incoming[node] == 0]
        walked: list[str] = []
        while ready:
            node = ready.pop(0)
            walked.append(node)
            for nxt in outgoing[node]:
                incoming[nxt] -= 1
                if incoming[nxt] == 0:
                    ready.append(nxt)
        walked += [node for node in self.nodes if node not in walked]
        return walked


class SlideElement(BaseModel):
    """One addressable thing on a page — the unit the director points at."""

    id: str
    kind: ElementKind = ElementKind.OTHER
    text: str = ""
    bbox: BBox
    level: int = 0
    # Human-readable handle the LLM uses to refer to this element ("rag", "vector_db").
    label: str = ""
    # 0..1, how central this element is to the page's message.
    importance: float = 0.5
    asset_path: str | None = None
    # The numbers behind a chart, when this element is one.
    #
    # Kept on the element rather than re-read from the source file when it is
    # wanted. The source is a `.pptx` the user may have moved, and the project
    # is supposed to be the whole truth about the video — a chart that can only
    # be animated while the original file is still where it was is a chart that
    # stops animating for reasons nobody can see.
    #
    # Exact, not recognised: it comes out of the OOXML, so redrawing it cannot
    # change what the slide says. That is the whole reason a rebuilt chart is
    # allowed here at all (方案 §12).
    chart: ChartFacts | None = None


class ElementGroup(BaseModel):
    """Elements that read as one thing — a card, a label with its body, a row.

    Written by the understanding step, which sees the rendered page; the
    geometric grouping in the director (`_belongs`, `_labels`, `_names_the_
    block`, `_same_row`) reconstructs the same answer shape by shape and has
    been patched once per deck layout it had not met. Where a group exists it
    is authoritative; where it does not, geometry still answers.
    """

    #: Element ids, in page order. Only ids that exist on the page survive
    #: validation — an invented member would aim the camera at nothing.
    members: list[str] = Field(default_factory=list)
    #: The member that names the group ("" when none does).
    label: str = ""


class DocumentPage(BaseModel):
    index: int = Field(description="1-based page / slide number")
    page_type: PageType = PageType.CONTENT
    title: str = ""
    elements: list[SlideElement] = Field(default_factory=list)
    # The flow this page draws, when it declares one. None means "this page
    # has no readable structure", which is not the same as "this page has no
    # diagram" — an architecture drawn as a picture is still a picture, and
    # the honest thing to do with it is show it.
    diagram: DiagramFacts | None = None
    # How the page's elements group into things — a card, a label with its
    # body. From the understanding model; empty means nobody has said, and
    # geometry decides alone.
    groups: list[ElementGroup] = Field(default_factory=list)
    speaker_notes: str = ""
    # Rendered full-resolution page image, relative to the project directory.
    image_path: str | None = None
    width: float = 0
    height: float = 0
    summary: str = ""
    key_points: list[str] = Field(default_factory=list)

    def element(self, element_id: str) -> SlideElement | None:
        for el in self.elements:
            if el.id == element_id or el.label == element_id:
                return el
        return None

    def raw_text(self) -> str:
        return "\n".join(el.text for el in self.elements if el.text).strip()


class Section(BaseModel):
    """A narrative chapter spanning one or more pages."""

    title: str
    page_indexes: list[int] = Field(default_factory=list)
    summary: str = ""


class DocumentModel(BaseModel):
    title: str = ""
    language: str = "zh"
    topic: str = ""
    summary: str = ""
    key_concepts: list[str] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    pages: list[DocumentPage] = Field(default_factory=list)
    # Explaining order may differ from page order (e.g. skip a contact page).
    presentation_order: list[int] = Field(default_factory=list)

    def page(self, index: int) -> DocumentPage | None:
        for page in self.pages:
            if page.index == index:
                return page
        return None

    def ordered_pages(self) -> list[DocumentPage]:
        if not self.presentation_order:
            return sorted(self.pages, key=lambda p: p.index)
        by_index = {p.index: p for p in self.pages}
        return [by_index[i] for i in self.presentation_order if i in by_index]
