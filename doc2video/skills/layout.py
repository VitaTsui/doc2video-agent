"""presentation-layout — subtitles, safe areas, and page-to-frame geometry.

Everything about "where things sit in the frame" lives here, so the renderer
adapters can stay geometry-free and the director can keep thinking in page
coordinates.
"""

from __future__ import annotations

import re
from math import ceil

from ..schemas import BBox, DocumentPage, Scene, SubtitleCue

# One comfortable line of CJK subtitle at 1080p. Measured against a
# hand-written subtitle track for a deck of this kind: 78 captions, median 21
# characters, longest 32.
MAX_SUBTITLE_CHARS = 28
MIN_CUE_SECONDS = 0.8

# Where a caption may break: the marks a speaker actually stops on. `、` and
# `：` are not among them — they separate items *inside* a clause, and cutting
# there produced captions of four characters. The same hand-written track
# keeps its `、` and never cuts at one.
CLAUSE_SPLIT = re.compile(r"(?<=[，,。！？!?；;…])")
PUNCTUATION = "，,。！？!?；;、：:…—－·「」『』（）()《》〈〉【】\"'“”‘’ "


def build_subtitles(scene: Scene) -> list[SubtitleCue]:
    """Split each narration segment into readable cues within its own window."""
    cues: list[SubtitleCue] = []
    for segment in scene.segments:
        span = max(segment.end - segment.start, 0.0)
        if span <= 0 or not segment.text.strip():
            continue
        chunks = _chunk(segment.text.strip())
        cursor = segment.start
        for chunk, share in zip(chunks, _shares(chunks, span), strict=True):
            cursor_end = min(cursor + share, segment.end)
            cues.append(
                SubtitleCue(
                    start=round(cursor, 3),
                    end=round(cursor_end, 3),
                    text=chunk,
                    scene_id=scene.scene_id,
                )
            )
            cursor = cursor_end
            if cursor >= segment.end:
                break
    return cues


def _shares(chunks: list[str], span: float) -> list[float]:
    """Divide a segment's window among its cues, longer cue → longer on screen.

    A floor keeps a two-character cue from flashing past, but it is capped at
    an equal split: cutting at every mark can leave more cues in a segment than
    ``MIN_CUE_SECONDS`` each would fit, and a floor that overruns the window
    would push the tail cues past ``segment.end``, where they are dropped.
    """
    total = sum(len(c) for c in chunks) or 1
    floor = min(MIN_CUE_SECONDS, span / len(chunks))
    raw = [max(span * len(c) / total, floor) for c in chunks]
    scale = span / sum(raw)
    return [share * scale for share in raw]


def _chunk(text: str) -> list[str]:
    """A caption per line's worth of speech, with the punctuation left off.

    A comma becomes a cut rather than a character — but not every comma, and
    that was the mistake. Cutting at every mark gave 270 captions over a
    six-minute film, a median of seven characters, two seconds each, and
    thirty-eight that were gone in under a second. A hand-written track for
    the same kind of deck runs 78 captions at a median of 21 characters and
    4.6 seconds; its captions hold a whole thought.

    So clauses are cut at the marks a speaker stops on, and then joined back
    up to a line's worth. Joining does not re-run the pause the audio just
    took — the line simply stays on screen across it, which is what reading a
    subtitle is like. The comma is replaced by a space, again as the reference
    track does.
    """
    pieces = [stripped for p in CLAUSE_SPLIT.split(text) if (stripped := p.strip(PUNCTUATION))]

    # A clause longer than a line still has to be broken somewhere. Into equal
    # parts rather than off the front: taking a full line at a time leaves the
    # tail as a remainder, and one nine-character caption after two long ones
    # reads as a mistake.
    split: list[str] = []
    for chunk in pieces:
        parts = ceil(len(chunk) / MAX_SUBTITLE_CHARS)
        if parts <= 1:
            split.append(chunk)
            continue
        size = ceil(len(chunk) / parts)
        split.extend(chunk[at : at + size] for at in range(0, len(chunk), size))

    merged: list[str] = []
    for chunk in split:
        if merged and len(merged[-1]) + 1 + len(chunk) <= MAX_SUBTITLE_CHARS:
            merged[-1] = f"{merged[-1]} {chunk}"
        else:
            merged.append(chunk)
    return merged or [text.strip(PUNCTUATION) or text]


def to_frame_area(bbox: BBox, page: DocumentPage, width: int, height: int) -> BBox:
    """Map a page-pixel box to a 0..1 box in the rendered frame.

    The page is letterboxed into the frame, so the mapping has to account for
    the scale factor *and* the padding — otherwise every zoom target drifts.
    """
    page_w = page.width or float(width)
    page_h = page.height or float(height)
    scale = min(width / page_w, height / page_h)
    disp_w, disp_h = page_w * scale, page_h * scale
    off_x, off_y = (width - disp_w) / 2, (height - disp_h) / 2

    x = (off_x + bbox.x * scale) / width
    y = (off_y + bbox.y * scale) / height
    w = (bbox.w * scale) / width
    h = (bbox.h * scale) / height

    # Clamp into frame; a target partially off-screen would zoom to nowhere.
    x = max(0.0, min(1.0, x))
    y = max(0.0, min(1.0, y))
    w = max(0.02, min(1.0 - x, w))
    h = max(0.02, min(1.0 - y, h))
    return BBox(x=x, y=y, w=w, h=h)


# How much air to leave around a highlight, in frame pixels.
#
# In pixels rather than as a ratio of the box, because this one is *drawn*: an
# outline padded by 8% of each dimension separately gets 16px of air beside a
# wide line of text and 2px above it, which is what made the box look loose on
# the sides and clamped on the glyphs. `BBox.padded` stays as it is — a zoom
# target genuinely does want a proportional margin.
HIGHLIGHT_PAD_PX = 7.0


def with_highlight_padding(area: BBox, width: int, height: int) -> BBox:
    """The same amount of air on every side of a drawn outline."""
    dx, dy = HIGHLIGHT_PAD_PX / width, HIGHLIGHT_PAD_PX / height
    x = max(0.0, area.x - dx)
    y = max(0.0, area.y - dy)
    return BBox(
        x=x,
        y=y,
        w=min(1.0 - x, area.w + 2 * dx),
        h=min(1.0 - y, area.h + 2 * dy),
    )
