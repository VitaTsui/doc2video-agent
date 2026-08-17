"""Theme colour resolution.

python-pptx hands back a theme *slot* (``ACCENT_1``) rather than a colour, so a
deck's brand palette is invisible without reading the theme part ourselves.
Resolving it is the single biggest fidelity win for HTML rendering: without it
every corporate deck comes out black-on-white.
"""

from __future__ import annotations

import zipfile
from xml.etree import ElementTree

from ...core.logging import get_logger

log = get_logger(__name__)

A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

# OOXML scheme slots, in the order PowerPoint lists them.
SCHEME_SLOTS = (
    "dk1", "lt1", "dk2", "lt2",
    "accent1", "accent2", "accent3", "accent4", "accent5", "accent6",
    "hlink", "folHlink",
)

# MSO_THEME_COLOR member name -> scheme slot. `bg1`/`tx1` are aliases that
# PowerPoint maps onto lt1/dk1, which is why both spellings appear here.
THEME_COLOR_TO_SLOT = {
    "DARK_1": "dk1",
    "TEXT_1": "dk1",
    "LIGHT_1": "lt1",
    "BACKGROUND_1": "lt1",
    "DARK_2": "dk2",
    "TEXT_2": "dk2",
    "LIGHT_2": "lt2",
    "BACKGROUND_2": "lt2",
    "ACCENT_1": "accent1",
    "ACCENT_2": "accent2",
    "ACCENT_3": "accent3",
    "ACCENT_4": "accent4",
    "ACCENT_5": "accent5",
    "ACCENT_6": "accent6",
    "HYPERLINK": "hlink",
    "FOLLOWED_HYPERLINK": "folHlink",
}

DEFAULT_SCHEME = {
    "dk1": "#000000", "lt1": "#FFFFFF", "dk2": "#44546A", "lt2": "#E7E6E6",
    "accent1": "#4472C4", "accent2": "#ED7D31", "accent3": "#A5A5A5",
    "accent4": "#FFC000", "accent5": "#5B9BD5", "accent6": "#70AD47",
    "hlink": "#0563C1", "folHlink": "#954F72",
}


class Theme:
    """A deck's colour scheme, with tint/shade applied on lookup."""

    def __init__(self, scheme: dict[str, str] | None = None) -> None:
        self.scheme = {**DEFAULT_SCHEME, **(scheme or {})}

    def slot(self, name: str, brightness: float = 0.0) -> str:
        return apply_brightness(self.scheme.get(name, "#000000"), brightness)

    def resolve(self, color) -> str | None:
        """Turn a python-pptx ColorFormat into ``#RRGGBB``, or None if unset."""
        if color is None:
            return None
        try:
            color_type = color.type
        except (AttributeError, ValueError):
            return None
        if color_type is None:
            return None

        brightness = 0.0
        try:
            brightness = float(color.brightness or 0.0)
        except (AttributeError, ValueError, TypeError):
            brightness = 0.0

        # An explicit RGB value needs no scheme lookup.
        try:
            rgb = color.rgb
        except (AttributeError, ValueError, TypeError):
            rgb = None
        if rgb is not None:
            return apply_brightness(f"#{rgb}", brightness)

        try:
            member = color.theme_color
        except (AttributeError, ValueError):
            return None
        name = getattr(member, "name", None) or str(member)
        slot = THEME_COLOR_TO_SLOT.get(name)
        if slot is None:
            return None
        return self.slot(slot, brightness)


def load_theme(pptx_path) -> Theme:
    """Read the first theme part out of the .pptx package."""
    try:
        with zipfile.ZipFile(pptx_path) as archive:
            names = sorted(n for n in archive.namelist() if n.startswith("ppt/theme/theme"))
            if not names:
                return Theme()
            xml = archive.read(names[0])
    except (OSError, zipfile.BadZipFile) as exc:
        log.debug("读取主题失败，使用默认配色：%s", exc)
        return Theme()

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        log.debug("解析主题 XML 失败，使用默认配色：%s", exc)
        return Theme()

    scheme_el = root.find(f".//{A_NS}clrScheme")
    if scheme_el is None:
        return Theme()

    scheme: dict[str, str] = {}
    for slot in SCHEME_SLOTS:
        node = scheme_el.find(f"{A_NS}{slot}")
        if node is None:
            continue
        value = _colour_of(node)
        if value:
            scheme[slot] = value
    return Theme(scheme)


def _colour_of(node) -> str | None:
    srgb = node.find(f"{A_NS}srgbClr")
    if srgb is not None and srgb.get("val"):
        return f"#{srgb.get('val').upper()}"
    sys_clr = node.find(f"{A_NS}sysClr")
    if sys_clr is not None:
        # lastClr is the concrete value the authoring app resolved this to.
        last = sys_clr.get("lastClr")
        if last:
            return f"#{last.upper()}"
        return "#FFFFFF" if sys_clr.get("val") == "window" else "#000000"
    return None


def apply_brightness(hex_color: str, brightness: float) -> str:
    """PowerPoint's tint (positive) / shade (negative) adjustment."""
    if not brightness:
        return hex_color
    value = hex_color.lstrip("#")
    if len(value) != 6:
        return hex_color
    channels = [int(value[i : i + 2], 16) for i in (0, 2, 4)]
    if brightness > 0:
        channels = [round(c + (255 - c) * brightness) for c in channels]
    else:
        channels = [round(c * (1 + brightness)) for c in channels]
    return "#" + "".join(f"{max(0, min(255, c)):02X}" for c in channels)
