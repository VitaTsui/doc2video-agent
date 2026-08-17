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


# `<a:schemeClr val="...">` uses its own names for four of the slots.
SCHEME_CLR_ALIASES = {
    "tx1": "dk1",
    "bg1": "lt1",
    "tx2": "dk2",
    "bg2": "lt2",
}

DEFAULT_FONTS = {
    "major_latin": "Calibri Light",
    "major_ea": "",
    "minor_latin": "Calibri",
    "minor_ea": "",
}


class Theme:
    """A deck's colour scheme and font scheme, with tint/shade applied on lookup."""

    def __init__(
        self, scheme: dict[str, str] | None = None, fonts: dict[str, str] | None = None
    ) -> None:
        self.scheme = {**DEFAULT_SCHEME, **(scheme or {})}
        self.fonts = {**DEFAULT_FONTS, **(fonts or {})}

    def slot(self, name: str, brightness: float = 0.0) -> str:
        return apply_brightness(self.scheme.get(name, "#000000"), brightness)

    def font_ref(self, typeface: str | None) -> str | None:
        """Resolve a theme font reference such as ``+mn-ea`` to a real family."""
        if not typeface:
            return None
        mapping = {
            "+mj-lt": "major_latin",
            "+mj-ea": "major_ea",
            "+mn-lt": "minor_latin",
            "+mn-ea": "minor_ea",
        }
        key = mapping.get(typeface)
        if key is None:
            return typeface
        return self.fonts.get(key) or None

    def color_element(self, node) -> str | None:
        """Resolve a raw ``<a:srgbClr>`` / ``<a:schemeClr>`` element to ``#RRGGBB``.

        python-pptx only exposes colours through its own wrappers; the
        layout/master style chains are read as raw XML, so they need this.
        """
        if node is None:
            return None
        srgb = node.find(f"{A_NS}srgbClr")
        if srgb is not None and srgb.get("val"):
            return apply_brightness(f"#{srgb.get('val').upper()}", _brightness_of(srgb))
        scheme = node.find(f"{A_NS}schemeClr")
        if scheme is not None and scheme.get("val"):
            name = scheme.get("val")
            # phClr only has meaning inside a style definition being applied to
            # a shape; there is no concrete colour to resolve it to here.
            if name == "phClr":
                return None
            slot = SCHEME_CLR_ALIASES.get(name, name)
            return self.slot(slot, _brightness_of(scheme))
        return None

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

    scheme: dict[str, str] = {}
    scheme_el = root.find(f".//{A_NS}clrScheme")
    if scheme_el is not None:
        for slot in SCHEME_SLOTS:
            node = scheme_el.find(f"{A_NS}{slot}")
            if node is None:
                continue
            value = _colour_of(node)
            if value:
                scheme[slot] = value

    return Theme(scheme, _font_scheme(root))


def _font_scheme(root) -> dict[str, str]:
    """Major (headings) and minor (body) typefaces, latin and East Asian."""
    fonts: dict[str, str] = {}
    font_el = root.find(f".//{A_NS}fontScheme")
    if font_el is None:
        return fonts
    for prefix, tag in (("major", "majorFont"), ("minor", "minorFont")):
        group = font_el.find(f"{A_NS}{tag}")
        if group is None:
            continue
        for kind, child in (("latin", "latin"), ("ea", "ea")):
            node = group.find(f"{A_NS}{child}")
            if node is not None and node.get("typeface"):
                fonts[f"{prefix}_{kind}"] = node.get("typeface")
    return fonts


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


def _brightness_of(node) -> float:
    """Approximate OOXML tint/shade/lumMod as a single brightness delta."""
    for tag, sign in (("tint", 1.0), ("lumOff", 1.0), ("shade", -1.0)):
        child = node.find(f"{A_NS}{tag}")
        if child is not None and child.get("val"):
            try:
                # OOXML stores these as thousandths of a percent.
                value = int(child.get("val")) / 100000
            except ValueError:
                continue
            return sign * (1 - value) if tag != "lumOff" else value
    lum_mod = node.find(f"{A_NS}lumMod")
    if lum_mod is not None and lum_mod.get("val"):
        try:
            return int(lum_mod.get("val")) / 100000 - 1
        except ValueError:
            return 0.0
    return 0.0


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
