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
import re
import subprocess
from pathlib import Path

from ..core.logging import get_logger
from ..schemas import ActionType, BBox, DocumentPage, ReviewFinding, SubtitleCue, VideoProject
from ..tools import ffmpeg

log = get_logger(__name__)

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
# How often to look at the picture inside one scene. Every two seconds is a
# handful of samples for a typical scene — enough that a transition at each end
# cannot make the whole scene look empty, cheap enough to stay one pass per
# clip.
FRAME_SAMPLE_SECONDS = 2.0
# A frame whose brightest and darkest pixels are this close has nothing on it.
# Real slides land two hundred apart even when they are white with pale text.
BLANK_RANGE = 24.0
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


def check_frames(project: VideoProject, clip_path) -> list[ReviewFinding]:
    """Scenes that came out blank, found by looking at the picture.

    The one question geometry cannot answer. Every check above reasons about
    what the project says should be on screen; this one asks whether anything
    is. A page that failed to stage, an asset that did not copy, a renderer
    that wrote white — all of them leave a project that reviews perfectly and
    a video with nothing in it.

    Asked of each scene's own clip rather than of the finished film. Sampling
    the concatenation means deciding which scene a timestamp belongs to, and
    getting that wrong reports the wrong page — the first version did exactly
    that, naming the scene before the one whose frame it had measured. A clip
    knows which scene it is, and its middle is nowhere near a transition, so
    both questions disappear.
    """
    findings: list[ReviewFinding] = []
    for scene in project.scenes:
        clip = project.render.scene_clips.get(scene.scene_id)
        if not clip:
            continue
        path = clip_path(clip)
        if path is None or not Path(path).exists():
            continue
        spread = _brightest_moment(Path(path))
        if spread is None or spread > BLANK_RANGE:
            continue
        findings.append(
            ReviewFinding(
                severity="error",
                kind="blank_frame",
                scene_id=scene.scene_id,
                message=f"这一段画面是空的（明暗差 {spread:.0f}），没有画出东西",
            )
        )
    return findings


def _brightest_moment(clip: Path) -> float | None:
    """The most contrast this clip ever shows, or None if it cannot be read.

    The whole measurement: a frame with something on it spans two hundred
    levels even when the slide is white with pale text; a frame with nothing
    on it spans none. Taken as the maximum over several samples because a
    transition passes through blank on the way in and out — a scene is empty
    only if it is empty all the way through.

    Read in one linear pass per clip rather than by seeking. Seeking to a
    timestamp and asking for a single frame's statistics reports zero for
    frames that plainly have content — measured against the exported images,
    every scene of a working video came back "blank". The stats are sound; the
    seek is what is not.
    """
    cmd = [
        ffmpeg.binary_path(),
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        str(clip),
        "-vf",
        f"fps=1/{FRAME_SAMPLE_SECONDS},signalstats,metadata=print:file=-",
        "-an",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    except (subprocess.SubprocessError, OSError) as exc:
        log.debug("读取画面统计失败：%s", exc)
        return None

    best: float | None = None
    low: float | None = None
    for line in result.stdout.splitlines():
        if (value := re.search(r"\.YMIN=([0-9.]+)", line)) is not None:
            low = float(value.group(1))
        elif (value := re.search(r"\.YMAX=([0-9.]+)", line)) is not None and low is not None:
            spread = float(value.group(1)) - low
            best = spread if best is None else max(best, spread)
            low = None
    return best


# A patch of untouched slide changes by this much or less when the camera is
# holding still — which, on a rendered slide, means not at all.
STILL_ENOUGH = 6.0
# Below this the target did not change: nothing was drawn on it.
ACTION_MIN_CHANGE = 8.0


def check_actions(project: VideoProject, clip_path) -> list[ReviewFinding]:
    """Highlights and pointers the renderer was told to draw but did not.

    The model review can say the action points at an element that exists. It
    cannot say the renderer drew anything: an opacity that resolved to zero, a
    component that threw, a box positioned off screen all leave a project where
    every action is valid and a video where nothing is pointed at.

    Only under a still camera. While the frame is zooming or fading every pixel
    differs, and a highlight is a thin outline — measured against a drifting
    frame the outline changes *less* than the background does, and the check
    reports the opposite of the truth. Verified against a crop of the real
    frame: the outline was plainly there, and the naive difference called it
    missing.
    """
    findings: list[ReviewFinding] = []
    starts = {clip.scene_id: clip.start for clip in project.timeline.video}
    checked = skipped = 0

    for action in project.timeline.actions:
        if action.type not in (ActionType.HIGHLIGHT, ActionType.POINTER) or action.area is None:
            continue
        clip = project.render.scene_clips.get(action.scene_id)
        if not clip:
            continue
        path = clip_path(clip)
        if path is None or not Path(path).exists():
            continue

        offset = starts.get(action.scene_id, 0.0)
        before = action.start - offset - 0.3
        during = action.start - offset + min(action.end - action.start, 2.0) * 0.6
        if before < 0:
            continue
        # Whether the camera is holding still is measured, not inferred. A
        # zoom keeps easing after its window closes, so an action that the
        # timeline says is clear of one can still sit on a drifting frame —
        # and on a drifting frame every patch changes more than a thin
        # outline does, which is how the first version came to report the
        # opposite of what a crop of the same frame plainly showed.
        noise = _changed(Path(path), _elsewhere(action.area), before, during)
        if noise is None or noise > STILL_ENOUGH:
            skipped += 1
            continue
        drawn = _changed(Path(path), action.area, before, during)
        if drawn is None:
            continue
        checked += 1
        if drawn < ACTION_MIN_CHANGE:
            findings.append(
                ReviewFinding(
                    severity="warning",
                    kind="action_not_visible",
                    scene_id=action.scene_id,
                    message=(
                        f"{action.type.value} 指向 {action.target or '画面'}，"
                        f"但画面静止时那一块没有任何变化（{drawn:.0f}）——没画出来"
                    ),
                )
            )
    # Said out loud because the coverage is partial and the reason is not
    # obvious: most actions sit on a frame that is still easing out of a zoom,
    # and those cannot be judged this way at all.
    log.info("动作可见性：验证 %d 个，因画面在动跳过 %d 个", checked, skipped)
    return findings


def _elsewhere(area: BBox) -> BBox:
    """A patch the same size, somewhere the action is not — the noise floor."""
    x = 0.05 if area.x > 0.4 else 0.95 - area.w
    y = 0.05 if area.y > 0.4 else 0.95 - area.h
    return BBox(x=max(x, 0.0), y=max(y, 0.0), w=area.w, h=area.h)


def _changed(clip: Path, area: BBox, before: float, during: float) -> float | None:
    """Peak brightness change inside `area` between two moments of one clip.

    Peak rather than average: an outline is a few pixels wide, and averaged
    over its box it disappears into the unchanged middle.
    """
    frames = [_crop(clip, area, at) for at in (before, during)]
    if any(frame is None for frame in frames):
        return None
    cmd = [
        ffmpeg.binary_path(),
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        str(frames[0]),
        "-i",
        str(frames[1]),
        "-filter_complex",
        "[0][1]blend=all_mode=difference,signalstats,metadata=print:file=-",
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    except (subprocess.SubprocessError, OSError):
        return None
    finally:
        for frame in frames:
            if frame is not None:
                frame.unlink(missing_ok=True)
    peak = re.search(r"\.YMAX=([0-9.]+)", result.stdout)
    return float(peak.group(1)) if peak else None


def _crop(clip: Path, area: BBox, at: float) -> Path | None:
    """One frame of `clip` at `at`, cut down to `area`. Caller deletes it."""
    import tempfile

    handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    handle.close()
    out = Path(handle.name)
    crop = (
        f"crop=iw*{area.w:.6f}:ih*{area.h:.6f}:iw*{area.x:.6f}:ih*{area.y:.6f}"
    )
    cmd = [
        ffmpeg.binary_path(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(clip),
        "-vf",
        f"select='gte(t,{max(at, 0.0):.3f})',{crop}",
        "-frames:v",
        "1",
        str(out),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120, check=True)
    except (subprocess.SubprocessError, OSError):
        out.unlink(missing_ok=True)
        return None
    return out if out.exists() and out.stat().st_size > 0 else None
