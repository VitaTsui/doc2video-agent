"""What the viewer sees, checked against what the project meant.

The review beside this one reads the project: every page has a scene, every
scene has audio, every action points at an element that exists. All of that can
be true of a video that is unwatchable — a caption sitting on top of the number
it is describing, a scene that renders black, a highlight the renderer drew at
opacity zero. Those are not modelling mistakes; the model is right and the
frame is wrong, and nothing in the project can say so.

The rule this follows: **when the renderer's own geometry can answer the
question, do not go looking at pixels.** The subtitle band is laid out by
values this repository chooses — font size, line height, width, margin — so
whether a caption covers an important element is arithmetic, not computer
vision, and arithmetic does not need a frame to be extracted, decoded and
scanned. Pixels are for the questions geometry cannot answer: did the thing
actually get drawn.
"""

from __future__ import annotations

import math

from ..schemas import ActionType, BBox, DocumentPage, ReviewFinding, SubtitleCue, VideoProject

# The caption's own box, as `renderer/src/components/Subtitles.tsx` draws it.
# Duplicated here rather than imported because one is TypeScript and the other
# Python; a test asserts the two have not drifted apart.
SUBTITLE_FONT_PX = 40
SUBTITLE_LINE_HEIGHT = 1.35
SUBTITLE_MAX_WIDTH_RATIO = 0.80
SUBTITLE_PADDING_X = 28
SUBTITLE_PADDING_Y = 14

# A caption covering more than this of an element is covering it. Below it the
# overlap is a corner clipping a margin, which nobody notices.
COVER_RATIO = 0.25
# How much of the frame a caption may take. Leaving the frame entirely is the
# obvious failure and almost never happens; the one that does is a caption that
# grew to five or six lines and became a wall of text across the lower half.
# Both are the same defect from the viewer's side — the caption is no longer
# something you glance at — so both are reported the same way.
MAX_BAND_RATIO = 0.25
# Only elements that matter: the caption sits at the bottom of every frame, and
# a footer or a page number underneath it is not a problem worth reporting.
PROTECTED_IMPORTANCE = 0.5


def subtitle_box(cue: SubtitleCue, width: int, height: int, margin: float) -> BBox:
    """Where this caption will be drawn, in frame pixels.

    Centred horizontally, sitting `margin` of the frame height off the bottom,
    wrapping at 80% of the frame width — the same rules the component uses.
    """
    usable = width * SUBTITLE_MAX_WIDTH_RATIO - 2 * SUBTITLE_PADDING_X
    text_px = _text_width(cue.text)
    lines = max(1, math.ceil(text_px / usable)) if usable > 0 else 1

    box_w = min(text_px, usable) + 2 * SUBTITLE_PADDING_X
    box_h = lines * SUBTITLE_FONT_PX * SUBTITLE_LINE_HEIGHT + 2 * SUBTITLE_PADDING_Y
    bottom = height - height * margin
    return BBox(x=(width - box_w) / 2, y=bottom - box_h, w=box_w, h=box_h)


def _text_width(text: str) -> float:
    """Rendered width at the caption's size, counting CJK as full-width."""
    wide = sum(1 for ch in text if "⺀" <= ch <= "鿿" or "＀" <= ch <= "￯")
    return wide * SUBTITLE_FONT_PX + (len(text) - wide) * SUBTITLE_FONT_PX * 0.55


def check_subtitles(
    project: VideoProject, width: int, height: int, margin: float
) -> list[ReviewFinding]:
    """Captions that leave the frame, or land on something worth reading."""
    findings: list[ReviewFinding] = []
    pages = {page.index: page for page in project.document.pages}
    scenes = {scene.scene_id: scene for scene in project.scenes}

    reported_overflow: set[str] = set()
    reported_cover: set[tuple[str, str]] = set()

    for cue in project.timeline.subtitles:
        box = subtitle_box(cue, width, height, margin)
        too_tall = box.h > height * MAX_BAND_RATIO
        if box.y < 0 or box.x < 0 or box.y + box.h > height or too_tall:
            if cue.scene_id not in reported_overflow:
                reported_overflow.add(cue.scene_id)
                lines = round(box.h / (SUBTITLE_FONT_PX * SUBTITLE_LINE_HEIGHT))
                findings.append(
                    ReviewFinding(
                        severity="error",
                        kind="subtitle_overflow",
                        scene_id=cue.scene_id,
                        message=(
                            f"字幕占了画面 {box.h / height:.0%}（{lines} 行）："
                            f"「{cue.text[:14]}…」"
                        ),
                    )
                )
            continue

        scene = scenes.get(cue.scene_id)
        page = pages.get(scene.source_page) if scene is not None else None
        if page is None:
            continue

        # While the camera is zoomed the frame is a crop of the page, so every
        # element is somewhere else — checked against the flat layout, a
        # caption reads as covering something that is no longer under it.
        # Found by pulling the frame and looking: the first version reported a
        # cover on a shot where the element had been zoomed off screen.
        zoom = _zoom_at(project, cue)
        for element in _protected(page):
            at = _to_frame(element.bbox, page, width, height)
            if zoom is not None:
                at = _through_zoom(at, zoom, width, height)
                if at is None:
                    continue
            covered = _cover_ratio(box, at)
            key = (cue.scene_id, element.id)
            if covered >= COVER_RATIO and key not in reported_cover:
                reported_cover.add(key)
                findings.append(
                    ReviewFinding(
                        severity="warning",
                        kind="subtitle_cover",
                        scene_id=cue.scene_id,
                        message=(
                            f"字幕挡住了「{element.label or element.text[:10]}」"
                            f"（遮住 {covered:.0%}）"
                        ),
                    )
                )
    return findings


def _zoom_at(project: VideoProject, cue: SubtitleCue) -> BBox | None:
    """The area the camera is showing while this caption is up, if zoomed.

    The middle of the cue rather than its start: a zoom that begins halfway
    through a caption leaves most of it over the flat page.
    """
    middle = (cue.start + cue.end) / 2
    for action in project.timeline.actions:
        if action.type is not ActionType.ZOOM or action.area is None:
            continue
        if action.start <= middle < action.end:
            return action.area
    return None


def _through_zoom(box: BBox, area: BBox, width: int, height: int) -> BBox | None:
    """Where a frame-space box lands once the camera fills the frame with `area`.

    None when it lands outside — an element the zoom pushed off screen cannot
    be covered by anything.
    """
    if area.w <= 0 or area.h <= 0:
        return None
    scale = min(width / (area.w * width), height / (area.h * height))
    moved = BBox(
        x=(box.x - area.x * width) * scale,
        y=(box.y - area.y * height) * scale,
        w=box.w * scale,
        h=box.h * scale,
    )
    if moved.x + moved.w <= 0 or moved.x >= width or moved.y + moved.h <= 0 or moved.y >= height:
        return None
    return moved


def _protected(page: DocumentPage) -> list:
    """Elements worth not covering: the ones the director may point at."""
    return [
        element
        for element in page.elements
        if element.importance >= PROTECTED_IMPORTANCE and (element.text or element.asset_path)
    ]


def _to_frame(bbox: BBox, page: DocumentPage, width: int, height: int) -> BBox:
    """A page-space box in frame pixels.

    The page is drawn to fit, so the same letterboxing the renderer applies has
    to be applied here — a box compared against an un-letterboxed frame is
    compared against a picture nobody sees.
    """
    if page.width <= 0 or page.height <= 0:
        return bbox
    scale = min(width / page.width, height / page.height)
    offset_x = (width - page.width * scale) / 2
    offset_y = (height - page.height * scale) / 2
    return BBox(
        x=bbox.x * scale + offset_x,
        y=bbox.y * scale + offset_y,
        w=bbox.w * scale,
        h=bbox.h * scale,
    )


def _cover_ratio(box: BBox, target: BBox) -> float:
    """How much of `target` the caption covers, 0..1."""
    if target.w <= 0 or target.h <= 0:
        return 0.0
    overlap_w = max(0.0, min(box.x + box.w, target.x + target.w) - max(box.x, target.x))
    overlap_h = max(0.0, min(box.y + box.h, target.y + target.h) - max(box.y, target.y))
    return overlap_w * overlap_h / (target.w * target.h)
