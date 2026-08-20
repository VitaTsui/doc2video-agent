"""The frame, checked against what the project meant.

The review beside this one reads the project and can be entirely satisfied by a
video nobody can watch: the caption is timed right, split right, and sitting on
top of the number being described. Nothing in the project says so, because in
the project the caption and the number are not in the same coordinate system.
"""

from __future__ import annotations

import pytest

from doc2video.schemas import (
    ActionCue,
    ActionType,
    BBox,
    DocumentPage,
    Scene,
    SlideElement,
    Source,
    SourceType,
    SubtitleCue,
    VideoProject,
)
from doc2video.skills import render_review

WIDTH, HEIGHT, MARGIN = 1920, 1080, 0.03


def _project(element: SlideElement, cue_text: str = "我们也在一线参与制定") -> VideoProject:
    page = DocumentPage(index=1, title="数据集", width=1920, height=1080, elements=[element])
    project = VideoProject(
        project_id="proj_frame",
        source=Source(type=SourceType.PPTX, file="d.pptx", path="source/d.pptx", page_count=1),
    )
    project.document.pages = [page]
    project.scenes = [Scene(scene_id="scene_01", source_page=1, narration="讲", duration=8)]
    project.timeline.subtitles = [
        SubtitleCue(start=1.0, end=3.0, text=cue_text, scene_id="scene_01")
    ]
    return project


def _element(y: float, text: str = "16+数据来源 丰富频次") -> SlideElement:
    return SlideElement(
        id="e1",
        text=text,
        bbox=BBox(x=700, y=y, w=400, h=60),
        label=text,
        importance=0.8,
    )


def test_a_caption_on_top_of_what_it_describes_is_reported():
    """Measured off a real deck: the caption sat on the slide's bottom row."""
    project = _project(_element(y=1000))
    findings = render_review.check_subtitles(project, WIDTH, HEIGHT, MARGIN)

    assert [f.kind for f in findings] == ["subtitle_cover"]
    assert "16+数据来源" in findings[0].message


def test_an_element_nowhere_near_the_caption_is_left_alone():
    """The caption sits at the bottom of every frame; most slides are fine."""
    assert render_review.check_subtitles(_project(_element(y=200)), WIDTH, HEIGHT, MARGIN) == []


def test_page_furniture_is_not_worth_reporting():
    """A footer under the caption is where footers go."""
    footer = SlideElement(
        id="e2", text="第 3 页", bbox=BBox(x=700, y=1000, w=400, h=60), label="第 3 页",
        importance=0.2,
    )
    assert render_review.check_subtitles(_project(footer), WIDTH, HEIGHT, MARGIN) == []


def test_a_zoomed_shot_is_not_judged_against_the_flat_page():
    """While the camera is zoomed the frame is a crop, so everything moved.

    Found by pulling the frame and looking: the first version reported a cover
    on a shot where the zoom had pushed that element off screen entirely.
    """
    project = _project(_element(y=1000))
    project.timeline.actions = [
        ActionCue(
            start=0.0,
            end=8.0,
            type=ActionType.ZOOM,
            scene_id="scene_01",
            # The camera fills the frame with the top-left quarter; the element
            # at the bottom of the page is not in shot at all.
            area=BBox(x=0.0, y=0.0, w=0.4, h=0.4),
        )
    ]
    assert render_review.check_subtitles(project, WIDTH, HEIGHT, MARGIN) == []


def test_a_caption_too_tall_for_the_frame_is_an_error():
    """Not a blemish: text that leaves the frame is text nobody can read."""
    project = _project(_element(y=200), cue_text="很长的一句话，" * 60)
    findings = render_review.check_subtitles(project, WIDTH, HEIGHT, MARGIN)

    assert [f.kind for f in findings] == ["subtitle_overflow"]
    assert findings[0].severity == "error"


def test_the_caption_geometry_matches_what_the_renderer_draws():
    """Two copies of one layout, in two languages, that must not drift.

    The box is computed here in Python and drawn there in TypeScript. A change
    to the component that does not reach this file turns every cover check into
    a guess about a caption that is no longer that size.
    """
    from pathlib import Path

    source = Path("renderer/src/components/Subtitles.tsx").read_text(encoding="utf-8")
    assert f"fontSize: {render_review.SUBTITLE_FONT_PX}" in source
    assert f"lineHeight: {render_review.SUBTITLE_LINE_HEIGHT}" in source
    assert f'maxWidth: "{int(render_review.SUBTITLE_MAX_WIDTH_RATIO * 100)}%"' in source
    assert (
        f"padding: \"{render_review.SUBTITLE_PADDING_Y}px {render_review.SUBTITLE_PADDING_X}px\""
        in source
    )


@pytest.mark.parametrize("text", ["短", "一句普通长度的中文字幕"])
def test_the_box_stays_inside_the_frame_for_ordinary_cues(text: str):
    cue = SubtitleCue(start=0, end=1, text=text, scene_id="scene_01")
    box = render_review.subtitle_box(cue, WIDTH, HEIGHT, MARGIN)
    assert box.x >= 0 and box.y > 0
    assert box.x + box.w <= WIDTH and box.y + box.h <= HEIGHT


def _make_clip(path, *, blank: bool) -> None:
    """A short clip with nothing on it, or with something."""
    import subprocess

    from doc2video.tools.media_binaries import ffmpeg

    source = "color=c=white:s=320x180:d=4:r=10" if blank else "testsrc=s=320x180:d=4:r=10"
    subprocess.run(
        [ffmpeg().path, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", source, "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


def test_a_scene_that_rendered_empty_is_caught(tmp_path):
    """The one question the project cannot answer about itself.

    Every other check reasons about what should be on screen. This asks whether
    anything is — a page that failed to stage, an asset that did not copy, a
    renderer that wrote white all leave a project that reviews perfectly.
    """
    blank, busy = tmp_path / "blank.mp4", tmp_path / "busy.mp4"
    _make_clip(blank, blank=True)
    _make_clip(busy, blank=False)

    project = _project(_element(y=200))
    project.scenes = [
        Scene(scene_id="scene_01", source_page=1, narration="一", duration=4),
        Scene(scene_id="scene_02", source_page=1, narration="二", duration=4),
    ]
    project.render.scene_clips = {"scene_01": "busy.mp4", "scene_02": "blank.mp4"}

    findings = render_review.check_frames(project, lambda rel: tmp_path / rel)

    assert [f.scene_id for f in findings] == ["scene_02"]
    assert findings[0].kind == "blank_frame"
    assert findings[0].severity == "error"


def test_a_missing_clip_is_not_reported_as_blank(tmp_path):
    """No clip is a different failure, and the model review already names it."""
    project = _project(_element(y=200))
    project.render.scene_clips = {"scene_01": "gone.mp4"}
    assert render_review.check_frames(project, lambda rel: tmp_path / rel) == []
