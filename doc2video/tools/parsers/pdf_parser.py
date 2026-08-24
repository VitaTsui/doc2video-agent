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

            with ledger.call(
                "parser:pymupdf",
                f"第 {page_number} 页",
                covers=[ledger.page_key(page_number)],
            ):
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

    for block in _split_columns(_joined_paragraphs(raw.get("blocks", []))):
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


# Two blocks belong to the same paragraph when they line up left, are the same
# size of type, and sit one line apart. The tolerances are in points, before
# the render zoom, and deliberately tight — a wrong merge joins two unrelated
# boxes into one that points at neither.
_SAME_LEFT_PT = 4.0
_SAME_SIZE_RATIO = 0.15
_LINE_GAP_RATIO = 0.9


def _joined_paragraphs(blocks: list[dict]) -> list[dict]:
    """Put a paragraph's lines back together when the PDF split them.

    Some generators emit every visual line as its own block. Taken at face
    value each line becomes an element, and two things go wrong at once: the
    text is cut mid-sentence (「…组织为可连续分」), and a highlight aimed at
    that sentence draws a box around one of its lines — which is the shape the
    viewer sees as "the box is on the wrong row".

    Merging is conservative: same left edge, same type size, the vertical gap
    no larger than a line. Anything else is left alone, because a paragraph
    wrongly joined to its neighbour aims the camera at the two of them.
    """
    merged: list[dict] = []
    for block in blocks:
        if block.get("type") == 1 or not block.get("lines"):
            merged.append(block)
            continue
        previous = merged[-1] if merged else None
        if previous is not None and _continues(previous, block):
            previous["lines"] = list(previous["lines"]) + list(block["lines"])
            px0, py0, px1, py1 = previous["bbox"]
            bx0, by0, bx1, by1 = block["bbox"]
            previous["bbox"] = (min(px0, bx0), min(py0, by0), max(px1, bx1), max(py1, by1))
            continue
        merged.append(dict(block))
    return merged


# A gap this wide inside one block is not word spacing — it is the corridor
# between two columns. In points, before the render zoom; roughly a centimetre
# on an A4 page, and three times the widest ordinary word space.
_COLUMN_GAP_PT = 24.0


def _split_columns(blocks: list[dict]) -> list[dict]:
    """Cut a block in two where it reaches across a gap between columns.

    PyMuPDF groups by proximity, and on a four-card layout the two card numbers
    sitting at the same height come back as one block: 「01 02」, 970 points
    wide on a 1920-point page. Nothing downstream can tell that apart from a
    genuinely wide heading — so a highlight on the left card grew a box that
    reached across the page and framed the right card's number too, which is
    what a viewer sees as 「框选框到隔壁去了」.

    Only horizontal gaps, and only within a line: two lines of a paragraph are
    someone else's job (`_joined_paragraphs`), and cutting a block vertically
    here would undo it.
    """
    out: list[dict] = []
    for block in blocks:
        if block.get("type") == 1 or not block.get("lines"):
            out.append(block)
            continue
        groups = _column_groups(block["lines"])
        if len(groups) < 2:
            out.append(block)
            continue
        for lines in groups:
            boxes = [line["bbox"] for line in lines]
            out.append(
                {
                    **block,
                    "lines": lines,
                    "bbox": (
                        min(b[0] for b in boxes),
                        min(b[1] for b in boxes),
                        max(b[2] for b in boxes),
                        max(b[3] for b in boxes),
                    ),
                }
            )
    return out


def _column_groups(lines: list[dict]) -> list[list[dict]]:
    """Split a block's pieces into columns, where a wide gap separates them.

    A line that itself straddles the gap is split into pieces first, so a
    single line reading 「01      02」 becomes two.
    """
    pieces: list[dict] = []
    for line in lines:
        pieces.extend(_split_line(line))
    if len(pieces) < 2:
        return [pieces] if pieces else []

    pieces.sort(key=lambda piece: piece["bbox"][0])
    groups: list[list[dict]] = [[pieces[0]]]
    for piece in pieces[1:]:
        edge = max(one["bbox"][2] for one in groups[-1])
        if piece["bbox"][0] - edge > _COLUMN_GAP_PT:
            groups.append([piece])
        else:
            groups[-1].append(piece)
    return groups


def _split_line(line: dict) -> list[dict]:
    """One line's spans, cut where they reach across a column gap."""
    spans = [span for span in line.get("spans", []) if span.get("text", "").strip()]
    if len(spans) < 2:
        return [line] if spans else []

    runs: list[list[dict]] = [[spans[0]]]
    for span in spans[1:]:
        if span["bbox"][0] - runs[-1][-1]["bbox"][2] > _COLUMN_GAP_PT:
            runs.append([span])
        else:
            runs[-1].append(span)
    if len(runs) < 2:
        return [line]
    return [
        {
            **line,
            "spans": run,
            "bbox": (
                min(s["bbox"][0] for s in run),
                min(s["bbox"][1] for s in run),
                max(s["bbox"][2] for s in run),
                max(s["bbox"][3] for s in run),
            ),
        }
        for run in runs
    ]


def _continues(previous: dict, block: dict) -> bool:
    """Whether `block` is the next line of the paragraph `previous` ends."""
    if previous.get("type") == 1 or not previous.get("lines"):
        return False
    px0, _, px1, py1 = previous["bbox"]
    bx0, by0, bx1, _ = block["bbox"]
    if abs(px0 - bx0) > _SAME_LEFT_PT:
        return False
    # Below it, by no more than a line's worth of space. `by0 < py1` would be
    # an overlap — two columns, not two lines.
    size = max(_max_size(previous), _max_size(block))
    if not size or not (0 <= by0 - py1 <= size * _LINE_GAP_RATIO):
        return False
    if abs(_max_size(previous) - _max_size(block)) > size * _SAME_SIZE_RATIO:
        return False
    # Comparable width: a one-word line under a full-width paragraph is a
    # caption or a heading, not its continuation.
    return min(px1 - px0, bx1 - bx0) >= 0.35 * max(px1 - px0, bx1 - bx0)


def _max_size(block: dict) -> float:
    sizes = (
        float(span.get("size", 0))
        for line in block.get("lines", [])
        for span in line.get("spans", [])
    )
    return max(sizes, default=0.0)


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
