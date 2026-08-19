"""PDF parser built on PyMuPDF.

Produces per-page high-resolution renders plus text/image elements with bounding
boxes expressed in *rendered pixel* coordinates, so the director's zoom targets
line up with what the renderer actually draws.
"""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF

from ...core import ledger
from ...core.ids import element_id
from ...core.logging import get_logger
from ...schemas import BBox, DocumentModel, DocumentPage, ElementKind, SlideElement

log = get_logger(__name__)

# Blocks smaller than this (in rendered pixels) are decorative noise, not targets.
MIN_ELEMENT_SIDE = 12.0


def parse_pdf(path: Path, assets_dir: Path, *, target_width: int = 1920) -> DocumentModel:
    ledger.used("parser:pymupdf")
    doc = fitz.open(path)
    pages: list[DocumentPage] = []

    try:
        for page_number, page in enumerate(doc, start=1):
            zoom = target_width / page.rect.width if page.rect.width else 1.0
            matrix = fitz.Matrix(zoom, zoom)

            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            image_name = f"page_{page_number:03d}.png"
            pixmap.save(assets_dir / image_name)

            elements = _extract_elements(page, page_number, zoom)
            title = _guess_title(elements)

            pages.append(
                DocumentPage(
                    index=page_number,
                    title=title,
                    elements=elements,
                    image_path=f"assets/{image_name}",
                    width=float(pixmap.width),
                    height=float(pixmap.height),
                )
            )
    finally:
        doc.close()

    log.info("解析 PDF 完成：%s，共 %d 页", path.name, len(pages))
    return DocumentModel(
        title=path.stem,
        pages=pages,
        presentation_order=[p.index for p in pages],
    )


def _extract_elements(page: fitz.Page, page_number: int, zoom: float) -> list[SlideElement]:
    raw = page.get_text("dict")
    candidates: list[tuple[float, SlideElement]] = []
    seq = 0

    for block in raw.get("blocks", []):
        bbox = _scaled_bbox(block.get("bbox"), zoom)
        if bbox is None:
            continue

        if block.get("type") == 1:  # image block
            seq += 1
            candidates.append(
                (
                    0.0,
                    SlideElement(
                        id=element_id(page_number, seq, "image"),
                        kind=ElementKind.IMAGE,
                        bbox=bbox,
                        label=f"image_{seq}",
                        importance=0.5,
                    ),
                )
            )
            continue

        text, max_size = _block_text(block)
        if not text:
            continue
        seq += 1
        candidates.append(
            (
                max_size,
                SlideElement(
                    id=element_id(page_number, seq, text),
                    kind=ElementKind.PARAGRAPH,
                    text=text,
                    bbox=bbox,
                    label=_short_label(text),
                ),
            )
        )

    if not candidates:
        return []

    # Font size is the only reliable structural signal in a flat PDF: the largest
    # text on the page is the title, noticeably-larger-than-body text is a heading.
    text_sizes = [size for size, el in candidates if el.kind is not ElementKind.IMAGE and size > 0]
    if text_sizes:
        largest = max(text_sizes)
        median = sorted(text_sizes)[len(text_sizes) // 2]
        for size, el in candidates:
            if el.kind is ElementKind.IMAGE:
                continue
            if size >= largest - 0.5:
                el.kind = ElementKind.TITLE
                el.importance = 0.9
            elif size > median * 1.15:
                el.kind = ElementKind.SUBTITLE
                el.importance = 0.7
            elif _looks_like_bullet(el.text):
                el.kind = ElementKind.BULLET
                el.importance = 0.6
            elif _looks_numeric(el.text):
                el.kind = ElementKind.NUMBER
                el.importance = 0.8

    return [el for _, el in candidates]


def _block_text(block: dict) -> tuple[str, float]:
    parts: list[str] = []
    max_size = 0.0
    for line in block.get("lines", []):
        line_text = "".join(span.get("text", "") for span in line.get("spans", []))
        if line_text.strip():
            parts.append(line_text.strip())
        for span in line.get("spans", []):
            max_size = max(max_size, float(span.get("size", 0)))
    return (" ".join(parts).strip(), max_size)


def _scaled_bbox(raw_bbox, zoom: float) -> BBox | None:
    if not raw_bbox:
        return None
    x0, y0, x1, y1 = (float(v) * zoom for v in raw_bbox)
    w, h = x1 - x0, y1 - y0
    if w < MIN_ELEMENT_SIDE or h < MIN_ELEMENT_SIDE:
        return None
    return BBox(x=x0, y=y0, w=w, h=h)


def _guess_title(elements: list[SlideElement]) -> str:
    for el in elements:
        if el.kind is ElementKind.TITLE and el.text:
            return el.text
    for el in elements:
        if el.text:
            return el.text[:60]
    return ""


def _looks_like_bullet(text: str) -> bool:
    return text.lstrip().startswith(("•", "-", "·", "▪", "◦", "*"))


def _looks_numeric(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > 24:
        return False
    digits = sum(ch.isdigit() for ch in stripped)
    return digits >= 1 and digits / len(stripped) >= 0.35


def _short_label(text: str) -> str:
    return text.strip().split("\n")[0][:24]
