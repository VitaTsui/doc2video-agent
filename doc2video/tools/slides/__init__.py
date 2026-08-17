"""Slide rasterization: turn PPTX styling into page images.

Three backends, tried in descending fidelity — see ``ppt_parser``:
LibreOffice (PowerPoint's own layout) > Chromium (this package) > Pillow.
"""

from .chromium import ChromiumSlideRenderer
from .describe import describe_chart
from .extract import chart_of, extract_deck
from .model import SlideDeck
from .theme import Theme, load_theme

__all__ = [
    "ChromiumSlideRenderer",
    "SlideDeck",
    "Theme",
    "chart_of",
    "describe_chart",
    "extract_deck",
    "load_theme",
]
