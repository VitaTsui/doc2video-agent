"""Pattern fills as CSS.

PowerPoint's 54 preset patterns are two-colour hatches: a foreground pattern
over a background. Dropping them (what we did before) turns a hatched shape
transparent, which reads as a *different* shape rather than a slightly wrong
one — so every preset resolves to something here.

Three families cover almost all of them:

* **hatches** — lines at some angle, drawn with ``repeating-linear-gradient``;
  two crossed sets give the grid and trellis presets.
* **dots and checkers** — ``radial-gradient`` / ``conic-gradient`` tiles.
* **percent shading** — literally "n% of the pixels are foreground". At slide
  scale that is indistinguishable from a blend, and a blend survives scaling
  and video compression far better than a 5% dot screen.

Anything unmapped falls back to a blend too, which is never *right* but is
always in the right colour family.
"""

from __future__ import annotations

from dataclasses import dataclass

# Percent presets: the share of the foreground colour in the blend.
PERCENT_PATTERNS = {
    "PERCENT_5": 0.05,
    "PERCENT_10": 0.10,
    "PERCENT_20": 0.20,
    "PERCENT_25": 0.25,
    "PERCENT_30": 0.30,
    # python-pptx spells 40% "ERCENT_40"; keep both so the deck still renders.
    "ERCENT_40": 0.40,
    "PERCENT_40": 0.40,
    "PERCENT_50": 0.50,
    "PERCENT_60": 0.60,
    "PERCENT_70": 0.70,
    "PERCENT_75": 0.75,
    "PERCENT_80": 0.80,
    "PERCENT_90": 0.90,
}


@dataclass(frozen=True)
class Hatch:
    """Line sets: ``angles`` in CSS degrees, ``line`` and ``period`` in px."""

    angles: tuple[int, ...]
    line: float
    period: float


@dataclass(frozen=True)
class Dots:
    radius: float
    period: float


@dataclass(frozen=True)
class Checker:
    period: float


# CSS angles: 0deg draws the gradient upward, so horizontal lines come from a
# 0deg gradient and vertical ones from 90deg. 45deg is the "upward" diagonal.
HATCHES: dict[str, Hatch] = {
    "LIGHT_HORIZONTAL": Hatch((0,), 1, 6),
    "HORIZONTAL": Hatch((0,), 2, 7),
    "DARK_HORIZONTAL": Hatch((0,), 3, 7),
    "NARROW_HORIZONTAL": Hatch((0,), 1, 4),
    "LIGHT_VERTICAL": Hatch((90,), 1, 6),
    "VERTICAL": Hatch((90,), 2, 7),
    "DARK_VERTICAL": Hatch((90,), 3, 7),
    "NARROW_VERTICAL": Hatch((90,), 1, 4),
    "LIGHT_UPWARD_DIAGONAL": Hatch((45,), 1, 6),
    "UPWARD_DIAGONAL": Hatch((45,), 2, 7),
    "DARK_UPWARD_DIAGONAL": Hatch((45,), 3, 7),
    "WIDE_UPWARD_DIAGONAL": Hatch((45,), 3, 11),
    "LIGHT_DOWNWARD_DIAGONAL": Hatch((135,), 1, 6),
    "DOWNWARD_DIAGONAL": Hatch((135,), 2, 7),
    "DARK_DOWNWARD_DIAGONAL": Hatch((135,), 3, 7),
    "WIDE_DOWNWARD_DIAGONAL": Hatch((135,), 3, 11),
    # Dashed presets are dashes along the same axis; at slide scale a thinner
    # continuous line is closer than anything we could tile.
    "DASHED_HORIZONTAL": Hatch((0,), 1, 8),
    "DASHED_VERTICAL": Hatch((90,), 1, 8),
    "DASHED_UPWARD_DIAGONAL": Hatch((45,), 1, 8),
    "DASHED_DOWNWARD_DIAGONAL": Hatch((135,), 1, 8),
    # Crossed sets.
    "CROSS": Hatch((0, 90), 1, 6),
    "SMALL_GRID": Hatch((0, 90), 1, 5),
    "LARGE_GRID": Hatch((0, 90), 1, 10),
    "DIAGONAL_CROSS": Hatch((45, 135), 1, 8),
    "TRELLIS": Hatch((45, 135), 2, 6),
    "WEAVE": Hatch((45, 135), 2, 8),
    "PLAID": Hatch((0, 90), 3, 10),
    # Brick / shingle / wave families are offset line courses; a single hatch in
    # the dominant direction keeps the texture without pretending to be exact.
    "HORIZONTAL_BRICK": Hatch((0,), 2, 8),
    "DIAGONAL_BRICK": Hatch((135,), 2, 8),
    "SHINGLE": Hatch((135,), 1, 8),
    "WAVE": Hatch((0,), 1, 7),
    "ZIG_ZAG": Hatch((45,), 1, 6),
    "DIVOT": Hatch((45,), 1, 7),
}

DOT_PATTERNS: dict[str, Dots] = {
    "DOTTED_GRID": Dots(1.0, 6),
    "DOTTED_DIAMOND": Dots(1.0, 5),
    "SMALL_CONFETTI": Dots(1.0, 5),
    "LARGE_CONFETTI": Dots(2.0, 7),
    "SPHERE": Dots(2.5, 8),
    "SOLID_DIAMOND": Dots(3.0, 8),
    "OUTLINED_DIAMOND": Dots(1.5, 8),
}

CHECKERS: dict[str, Checker] = {
    "SMALL_CHECKER_BOARD": Checker(6),
    "LARGE_CHECKER_BOARD": Checker(12),
}


def pattern_css(name: str, fore: str, back: str) -> str:
    """A CSS ``background`` value reproducing preset ``name``.

    ``fore`` and ``back`` are ``#RRGGBB``. The result is always non-empty: an
    unmapped preset still yields a blend rather than nothing.
    """
    if name in PERCENT_PATTERNS:
        return mix(fore, back, PERCENT_PATTERNS[name])

    hatch = HATCHES.get(name)
    if hatch is not None:
        layers = [
            f"repeating-linear-gradient({angle}deg, {fore} 0 {hatch.line:g}px,"
            f" transparent {hatch.line:g}px {hatch.period:g}px)"
            for angle in hatch.angles
        ]
        # The background colour goes last: CSS paints layers front to back.
        return ", ".join([*layers, f"linear-gradient({back}, {back})"])

    dots = DOT_PATTERNS.get(name)
    if dots is not None:
        return (
            f"radial-gradient({fore} {dots.radius:g}px, transparent {dots.radius:g}px)"
            f" 0 0 / {dots.period:g}px {dots.period:g}px,"
            f" linear-gradient({back}, {back})"
        )

    checker = CHECKERS.get(name)
    if checker is not None:
        return (
            f"conic-gradient({fore} 25%, {back} 0 50%, {fore} 0 75%, {back} 0)"
            f" 0 0 / {checker.period:g}px {checker.period:g}px,"
            f" linear-gradient({back}, {back})"
        )

    # MIXED, and anything a future PowerPoint adds.
    return mix(fore, back, 0.5)


def mix(fore: str, back: str, ratio: float) -> str:
    """Blend two ``#RRGGBB`` colours, ``ratio`` being the share of ``fore``."""
    fg, bg = _channels(fore), _channels(back)
    if fg is None or bg is None:
        return fore if fg is not None else back
    blended = [round(f * ratio + b * (1 - ratio)) for f, b in zip(fg, bg, strict=True)]
    return "#" + "".join(f"{max(0, min(255, c)):02X}" for c in blended)


def _channels(color: str) -> tuple[int, int, int] | None:
    value = (color or "").lstrip("#")
    if len(value) != 6:
        return None
    try:
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
    except ValueError:
        return None
