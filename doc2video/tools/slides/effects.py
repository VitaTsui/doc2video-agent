"""WordArt — the run-level effects python-pptx does not expose.

"WordArt" in a modern deck is not a separate object: it is ordinary text whose
``<a:rPr>`` carries an outline (``a:ln``), a gradient fill instead of a solid
one, and an effect list (shadow, glow). python-pptx surfaces the solid colour
and nothing else, so a WordArt title used to render as flat text — usually
white-on-white, because its visible colour came entirely from the outline.

Everything here maps onto CSS the browser can draw:

* outline → ``-webkit-text-stroke``
* gradient fill → a background clipped to the glyphs
* shadow / glow → ``text-shadow``

The one preset that has no CSS equivalent is ``a:prstTxWarp`` (text bent along
a path); warped text renders straight.
"""

from __future__ import annotations

import math

from pptx.oxml.ns import qn

from .model import TextEffects
from .theme import Theme

# OOXML angles are 60000ths of a degree, measured clockwise from "east".
ANGLE_UNITS = 60000
# Below this an outline is thinner than a rendered pixel and only muddies the glyph.
MIN_OUTLINE_PX = 0.4


def text_effects(run, theme: Theme, emu_to_px: float) -> TextEffects | None:
    """Read WordArt effects off a run, or None when it is plain text."""
    try:
        rpr = run._r.find(qn("a:rPr"))
    except AttributeError:
        return None
    if rpr is None:
        return None

    effects = TextEffects(
        outline_color=_outline_color(rpr, theme),
        outline_width_px=_outline_width(rpr, emu_to_px),
        gradient=gradient_css(rpr.find(qn("a:gradFill")), theme),
        shadow=_shadow(rpr.find(qn("a:effectLst")), theme, emu_to_px),
    )
    if effects.outline_color is None and effects.outline_width_px:
        # A width with no colour of its own means the theme line colour; without
        # resolving one, drawing a stroke would guess at the deck's palette.
        effects = effects.model_copy(update={"outline_width_px": 0.0})
    return effects if _has_any(effects) else None


def _has_any(effects: TextEffects) -> bool:
    return bool(
        (effects.outline_color and effects.outline_width_px)
        or effects.gradient
        or effects.shadow
    )


def _outline_color(rpr, theme: Theme) -> str | None:
    line = rpr.find(qn("a:ln"))
    if line is None or line.find(qn("a:noFill")) is not None:
        return None
    return color_with_alpha(line.find(qn("a:solidFill")), theme)


def _outline_width(rpr, emu_to_px: float) -> float:
    line = rpr.find(qn("a:ln"))
    if line is None:
        return 0.0
    try:
        width = int(line.get("w", "0")) * emu_to_px
    except ValueError:
        return 0.0
    return width if width >= MIN_OUTLINE_PX else 0.0


def _shadow(effect_lst, theme: Theme, emu_to_px: float) -> str | None:
    if effect_lst is None:
        return None

    shadows: list[str] = []

    outer = effect_lst.find(qn("a:outerShdw"))
    if outer is not None:
        color = color_with_alpha(outer, theme) or "rgba(0, 0, 0, 0.45)"
        distance = _emu(outer.get("dist"), emu_to_px)
        blur = _emu(outer.get("blurRad"), emu_to_px)
        try:
            radians = math.radians(int(outer.get("dir", "0")) / ANGLE_UNITS)
        except ValueError:
            radians = 0.0
        dx = distance * math.cos(radians)
        dy = distance * math.sin(radians)
        shadows.append(f"{dx:.1f}px {dy:.1f}px {blur:.1f}px {color}")

    glow = effect_lst.find(qn("a:glow"))
    if glow is not None:
        color = color_with_alpha(glow, theme) or "rgba(255, 255, 255, 0.75)"
        radius = _emu(glow.get("rad"), emu_to_px)
        # A glow is a halo, not a cast shadow: no offset, blur on all sides.
        shadows.append(f"0 0 {max(radius, 1):.1f}px {color}")

    return ", ".join(shadows) if shadows else None


def gradient_css(node, theme: Theme) -> str | None:
    """``<a:gradFill>`` as a CSS ``linear-gradient``."""
    if node is None:
        return None
    stops: list[str] = []
    gs_lst = node.find(qn("a:gsLst"))
    for stop in [] if gs_lst is None else gs_lst.findall(qn("a:gs")):
        color = color_with_alpha(stop, theme)
        if not color:
            continue
        try:
            position = int(stop.get("pos", "0")) / 1000
        except ValueError:
            position = 0.0
        stops.append(f"{color} {position:.0f}%")

    if len(stops) < 2:
        return None

    angle = 0.0
    lin = node.find(qn("a:lin"))
    if lin is not None:
        try:
            angle = int(lin.get("ang", "0")) / ANGLE_UNITS
        except ValueError:
            angle = 0.0
    # OOXML measures clockwise from the positive x-axis; CSS from "up".
    return f"linear-gradient({angle + 90:.0f}deg, {', '.join(stops)})"


def color_with_alpha(parent, theme: Theme) -> str | None:
    """Resolve a colour child, carrying ``<a:alpha>`` through as ``rgba()``."""
    if parent is None:
        return None
    hex_color = theme.color_element(parent)
    if not hex_color:
        return None

    for tag in ("a:srgbClr", "a:schemeClr"):
        node = parent.find(qn(tag))
        if node is None:
            continue
        alpha = node.find(qn("a:alpha"))
        if alpha is None:
            break
        try:
            share = int(alpha.get("val", "100000")) / 100000
        except ValueError:
            break
        if share >= 1:
            break
        value = hex_color.lstrip("#")
        channels = [int(value[i : i + 2], 16) for i in (0, 2, 4)]
        return f"rgba({channels[0]}, {channels[1]}, {channels[2]}, {share:.2f})"
    return hex_color


def _emu(value: str | None, emu_to_px: float) -> float:
    try:
        return int(value or "0") * emu_to_px
    except ValueError:
        return 0.0
