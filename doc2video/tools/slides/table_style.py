"""Table styles from ``ppt/tableStyles.xml``.

A table carries only a style *id*; the fills and text colours live in a separate
part that python-pptx does not expose. Without reading it the only options are
bare gridlines (further from the original than anything) or a guess built from
the theme accent. This resolves the real definition and keeps the guess as a
fallback for decks whose style id is not in the part.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree

from ...core.logging import get_logger
from .theme import A_NS, Theme

log = get_logger(__name__)

TABLE_STYLES_PART = "ppt/tableStyles.xml"


@dataclass(frozen=True)
class TableStyle:
    """The parts of a table style this renderer can actually draw."""

    header_fill: str | None = None
    header_text: str | None = None
    band_fill: str | None = None
    body_fill: str | None = None
    body_text: str | None = None
    border_color: str | None = None


class TableStyles:
    """Lookup of style-id -> TableStyle for one presentation."""

    def __init__(self, styles: dict[str, TableStyle], default_id: str | None = None) -> None:
        self._styles = styles
        self._default_id = default_id

    def get(self, style_id: str | None) -> TableStyle | None:
        if style_id:
            found = self._styles.get(style_id.upper())
            if found is not None:
                return found
        if self._default_id:
            return self._styles.get(self._default_id.upper())
        return None


def load_table_styles(pptx_path, theme: Theme) -> TableStyles:
    try:
        with zipfile.ZipFile(pptx_path) as archive:
            xml = archive.read(TABLE_STYLES_PART)
    except (OSError, KeyError, zipfile.BadZipFile):
        # The part is optional; many decks ship without it.
        return TableStyles({})

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        log.debug("解析 tableStyles.xml 失败：%s", exc)
        return TableStyles({})

    styles: dict[str, TableStyle] = {}
    for node in root.findall(f"{A_NS}tblStyle"):
        style_id = (node.get("styleId") or "").upper()
        if not style_id:
            continue
        styles[style_id] = _parse_style(node, theme)

    return TableStyles(styles, root.get("def"))


def _parse_style(node, theme: Theme) -> TableStyle:
    header = node.find(f"{A_NS}firstRow")
    band = node.find(f"{A_NS}band1H")
    whole = node.find(f"{A_NS}wholeTbl")

    return TableStyle(
        header_fill=_fill_of(header, theme),
        header_text=_text_color_of(header, theme),
        band_fill=_fill_of(band, theme),
        body_fill=_fill_of(whole, theme),
        body_text=_text_color_of(whole, theme),
        border_color=_border_of(whole, theme),
    )


def _fill_of(part, theme: Theme) -> str | None:
    if part is None:
        return None
    cell = part.find(f"{A_NS}tcStyle")
    if cell is None:
        return None
    fill = cell.find(f"{A_NS}fill")
    if fill is None:
        return None
    solid = fill.find(f"{A_NS}solidFill")
    return theme.color_element(solid) if solid is not None else None


def _text_color_of(part, theme: Theme) -> str | None:
    if part is None:
        return None
    text = part.find(f"{A_NS}tcTxStyle")
    return theme.color_element(text) if text is not None else None


def _border_of(part, theme: Theme) -> str | None:
    if part is None:
        return None
    cell = part.find(f"{A_NS}tcStyle")
    if cell is None:
        return None
    borders = cell.find(f"{A_NS}tcBdr")
    if borders is None:
        return None
    # Any edge will do — a table style rarely mixes colours across edges, and a
    # single border colour is all the renderer draws.
    for edge in ("left", "right", "top", "bottom", "insideH", "insideV"):
        node = borders.find(f"{A_NS}{edge}")
        if node is None:
            continue
        line = node.find(f"{A_NS}ln")
        if line is None:
            continue
        solid = line.find(f"{A_NS}solidFill")
        color = theme.color_element(solid) if solid is not None else None
        if color:
            return color
    return None
