"""Putting a page's elements into the order a person reads them.

A parser hands back elements in the order the file draws them, and a slide is
not drawn in the order it is read. Measured on one deck's page 8 — three
columns headed 市场 / 经营 / 发展 from left to right — the three headings came
back 经营, 发展, 市场: the middle column's, then the right's, then the left's,
because that is the order PowerPoint happened to lay them down. The bodies
underneath them came back in the other order, left to right, so the list
disagreed with itself.

Nothing downstream can recover from that. The writer is told 「按这个顺序讲」
and walks the page middle, right, left; the camera follows the sentences and
therefore jumps middle, right, left across the slide, which reads as the camera
losing its place — and the page was never being read wrong, it was being listed
wrong.

**Columns, not rows.** Sorting by `(y, x)` is the obvious answer and the wrong
one: in three columns it reads across all three, one line at a time, which is
how nobody has ever read a slide. Elements are grouped into columns by where
they sit horizontally, each column is read top to bottom, and the columns run
left to right.

**What is not in a column is what crosses them.** A title, a banner, a closing
line — measured by overlap rather than by width, because width alone does not
decide it: that deck's closing line covers 56% of the page and a threshold set
anywhere near there would swallow a column of a two-column layout. Crossing two
columns is the thing itself, and it is what a band does.
"""

from __future__ import annotations

from ...schemas import SlideElement

#: Wide enough that it cannot be helping to define where the columns are. The
#: columns are found from the narrow elements first, and everything else is
#: placed by whether it crosses them.
COLUMN_WIDTH_SHARE = 0.4

#: How far apart two elements' left edges can be and still be one column, as a
#: share of page width. Columns on a slide are far apart; a hanging indent is
#: not.
COLUMN_GAP = 0.12


def in_reading_order(
    elements: list[SlideElement], width: float, height: float
) -> list[SlideElement]:
    """`elements`, ordered the way the page is read.

    Falls back to the order given when there is nothing to go on — no page
    size, or elements without boxes — because a guess about layout is worse
    than the parser's own order.
    """
    if not elements or width <= 0 or height <= 0:
        return list(elements)
    if any(element.bbox is None for element in elements):
        return list(elements)

    # Where the columns are, decided by the text narrow enough to sit in one.
    # Text only: a slide's card backgrounds and icons are laid out around the
    # columns rather than in them, and letting them vote produced six
    # overlapping ranges on a three-column page.
    spans = _column_spans(
        [e for e in elements if e.text.strip() and e.bbox.w < width * COLUMN_WIDTH_SHARE],
        width,
    )

    bands: list[tuple[float, list[SlideElement]]] = []
    columnar: list[SlideElement] = []

    def flush() -> None:
        """Close the run of column elements collected so far."""
        if not columnar:
            return
        top = min(element.bbox.y for element in columnar)
        bands.append((top, _by_column(columnar, width)))
        columnar.clear()

    # An element that crosses more than one column belongs to none of them: it
    # splits the page into bands, and what falls between two bands is read as
    # columns.
    for element in sorted(elements, key=lambda e: (e.bbox.y, e.bbox.x)):
        if _crosses(element, spans) > 1:
            flush()
            bands.append((element.bbox.y, [element]))
            continue
        columnar.append(element)
    flush()

    bands.sort(key=lambda band: band[0])
    return [element for _top, group in bands for element in group]


def _column_spans(narrow: list[SlideElement], width: float) -> list[tuple[float, float]]:
    """The horizontal reach of each column, from the elements that sit inside one."""
    spans: list[list[float]] = []
    for element in sorted(narrow, key=lambda e: e.bbox.x):
        left, right = element.bbox.x, element.bbox.x + element.bbox.w
        if spans and left - spans[-1][0] <= width * COLUMN_GAP:
            spans[-1][1] = max(spans[-1][1], right)
            continue
        spans.append([left, right])
    return [(left, right) for left, right in spans]


def _crosses(element: SlideElement, spans: list[tuple[float, float]]) -> int:
    """How many columns this element reaches into."""
    left, right = element.bbox.x, element.bbox.x + element.bbox.w
    return sum(1 for start, end in spans if left < end and right > start)


def _by_column(elements: list[SlideElement], width: float) -> list[SlideElement]:
    """One run of elements, read column by column.

    Columns are found by left edge: a slide's columns sit far apart, so a gap
    wider than `COLUMN_GAP` of the page starts a new one. With one column this
    is just top-to-bottom, which is what a single-column page wants anyway.
    """
    columns: list[list[SlideElement]] = []
    for element in sorted(elements, key=lambda e: e.bbox.x):
        if columns and element.bbox.x - columns[-1][0].bbox.x <= width * COLUMN_GAP:
            columns[-1].append(element)
            continue
        columns.append([element])
    for column in columns:
        column.sort(key=lambda e: (e.bbox.y, e.bbox.x))
    return [element for column in columns for element in column]
