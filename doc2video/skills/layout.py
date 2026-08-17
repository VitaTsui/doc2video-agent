"""presentation-layout — subtitles, safe areas, and page-to-frame geometry.

Everything about "where things sit in the frame" lives here, so the renderer
adapters can stay geometry-free and the director can keep thinking in page
coordinates.
"""

from __future__ import annotations

import re

from ..schemas import BBox, DocumentPage, Scene, SubtitleCue
from ..tools.renderer.base import SUBTITLE_SAFE_BOTTOM

# Roughly one comfortable line of CJK subtitle at 1080p.
MAX_SUBTITLE_CHARS = 22
MIN_CUE_SECONDS = 0.8

CLAUSE_SPLIT = re.compile(r"(?<=[，,。！？!?；;、])")


def build_subtitles(scene: Scene) -> list[SubtitleCue]:
    """Split each narration segment into readable cues within its own window."""
    cues: list[SubtitleCue] = []
    for segment in scene.segments:
        span = max(segment.end - segment.start, 0.0)
        if span <= 0 or not segment.text.strip():
            continue
        chunks = _chunk(segment.text.strip())
        total = sum(len(c) for c in chunks) or 1
        cursor = segment.start
        for chunk in chunks:
            share = span * len(chunk) / total
            end = cursor + max(share, MIN_CUE_SECONDS if span > MIN_CUE_SECONDS else share)
            cues.append(
                SubtitleCue(
                    start=round(cursor, 3),
                    end=round(min(end, segment.end), 3),
                    text=chunk,
                    scene_id=scene.scene_id,
                )
            )
            cursor = end
            if cursor >= segment.end:
                break
    return cues


def _chunk(text: str) -> list[str]:
    """Break a sentence at punctuation, merging fragments up to the line limit."""
    pieces = [p for p in CLAUSE_SPLIT.split(text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if len(current) + len(piece) <= MAX_SUBTITLE_CHARS or not current:
            current += piece
        else:
            chunks.append(current.strip())
            current = piece
    if current.strip():
        chunks.append(current.strip())

    # A clause longer than the line limit still has to be broken somewhere.
    final: list[str] = []
    for chunk in chunks:
        while len(chunk) > MAX_SUBTITLE_CHARS * 1.4:
            final.append(chunk[:MAX_SUBTITLE_CHARS])
            chunk = chunk[MAX_SUBTITLE_CHARS:]
        if chunk:
            final.append(chunk)
    return final or [text]


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


def avoids_subtitle_band(area: BBox) -> BBox:
    """Nudge a highlight up when it would sit under the subtitle band."""
    limit = 1.0 - SUBTITLE_SAFE_BOTTOM
    if area.y + area.h <= limit:
        return area
    return BBox(x=area.x, y=area.y, w=area.w, h=max(0.02, limit - area.y))
