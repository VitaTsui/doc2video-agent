"""FFmpeg adapter filtergraph construction.

ffmpeg itself is an optional dependency, so these tests check the graph we build
rather than running it: quoting, enable windows and expression shape are where
this code actually goes wrong.
"""

from __future__ import annotations

import pytest

from doc2video.tools.renderer import ScenePlan
from doc2video.tools.renderer.base import PlanAction, PlanArea, PlanSubtitle
from doc2video.tools.renderer.ffmpeg_adapter import FFmpegAdapter, _escape_drawtext


def _plan(**overrides) -> ScenePlan:
    base = {
        "scene_id": "scene_01",
        "duration": 10.0,
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "image": "/tmp/page.png",
    }
    base.update(overrides)
    return ScenePlan(**base)


def test_base_graph_scales_and_pads():
    filters = FFmpegAdapter()._build_filters(_plan())
    graph = ",".join(filters)

    assert "scale=1920:1080:force_original_aspect_ratio=decrease" in graph
    assert "pad=1920:1080" in graph
    assert "setsar=1" in graph
    assert "fade=t=in" in graph


def test_zoom_becomes_a_zoompan_expression():
    plan = _plan(
        actions=[
            PlanAction(
                type="zoom", start=2.0, end=5.0, effect="zoom-highlight",
                target="e1", area=PlanArea(x=0.1, y=0.2, w=0.2, h=0.2),
            )
        ]
    )
    graph = ",".join(FFmpegAdapter()._build_filters(plan))

    assert "zoompan=" in graph
    # Frame-indexed window: 2.0s..5.0s at 30fps.
    assert "between(on,60,150)" in graph
    assert "s=1920x1080" in graph
    assert graph.count("'") % 2 == 0, "zoompan 表达式引号必须成对"


def test_highlight_and_pointer_become_time_gated_drawboxes():
    plan = _plan(
        actions=[
            PlanAction(type="highlight", start=1.0, end=4.0, effect="outline",
                       target="e1", area=PlanArea(x=0.25, y=0.5, w=0.5, h=0.25)),
            PlanAction(type="pointer", start=5.0, end=6.0, effect="pointer",
                       target="e2", area=PlanArea(x=0.4, y=0.4, w=0.1, h=0.1)),
        ]
    )
    filters = FFmpegAdapter()._build_filters(plan)
    boxes = [f for f in filters if f.startswith("drawbox=")]

    assert len(boxes) == 2
    assert "x=480:y=540:w=960:h=270" in boxes[0]
    assert "enable='between(t,1.000,4.000)'" in boxes[0]
    assert "t=fill" in boxes[1]


def test_subtitles_are_escaped_and_time_gated():
    plan = _plan(subtitles=[PlanSubtitle(start=0.0, end=2.0, text="比例: 50% 'quoted'")])
    filters = FFmpegAdapter()._build_filters(plan)
    drawtext = [f for f in filters if f.startswith("drawtext=")]

    if not drawtext:  # no CJK-capable font on this machine
        return
    assert "enable='between(t,0.000,2.000)'" in drawtext[0]
    assert "\\:" in drawtext[0]
    # A quote of our own would end the filter's text argument early.
    assert "'quoted'" not in drawtext[0]


def test_escape_drawtext_neutralizes_syntax_characters():
    escaped = _escape_drawtext("a:b'd\\e\nf")
    assert "\\:" in escaped
    assert "\\\\" in escaped
    assert "'" not in escaped
    assert "\n" not in escaped


def test_percent_survives_untouched():
    """Escaping it as \\% makes drawtext discard the whole cue ("Stray %")."""
    assert _escape_drawtext("增长 333%") == "增长 333%"


def test_subtitles_disable_template_expansion(monkeypatch: pytest.MonkeyPatch):
    """Without expansion=none, `%` and `{}` in a narration are read as syntax."""
    monkeypatch.setattr(
        "doc2video.tools.renderer.ffmpeg_adapter._find_font", lambda: "/tmp/font.ttc"
    )
    plan = _plan(subtitles=[PlanSubtitle(start=0.0, end=2.0, text="增长 333%")])
    drawtext = [f for f in FFmpegAdapter()._build_filters(plan) if f.startswith("drawtext=")]

    assert drawtext, "本机 ffmpeg 应支持 drawtext"
    assert "expansion=none" in drawtext[0]
    assert "333%" in drawtext[0]


def test_concat_puts_each_clip_where_its_frames_say(tmp_path):
    """The picture must land where the plan puts it, not where AAC padding does.

    `concat` offsets each input by the previous ones' *container* durations,
    and a clip's container is as long as its longest stream. Each clip carries
    a copy of its narration encoded to AAC, and an AAC frame does not divide
    evenly into a clip — so the container measured tens of milliseconds longer
    than the pictures in it, and every scene started that much later than the
    one before. Measured on a real thirty-scene film: page 27 arrived 1.37
    seconds after the plan, which is a highlight drawn while the *next*
    sentence is being spoken.
    """
    import json
    import subprocess

    from doc2video.tools import ffmpeg, media_binaries

    if not ffmpeg.available():
        pytest.skip("没有 ffmpeg")

    # As a rendered scene comes out: the picture is a whole number of frames
    # and the narration is whatever length the voice made it, so the audio
    # track overhangs the video by a few tens of milliseconds.
    narration = tmp_path / "narration.m4a"
    ffmpeg.run([
        "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
        "-t", "1.04", "-c:a", "aac", "-y", str(narration),
    ])
    clips = []
    for index in range(6):
        clip = tmp_path / f"{index}.mp4"
        ffmpeg.run([
            "-f", "lavfi", "-i", f"color=c=0x{index}0{index}0{index}0:s=160x90:r=30:d=1",
            "-i", str(narration),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy", "-y", str(clip),
        ])
        clips.append(clip)

    joined = ffmpeg.concat(clips, tmp_path / "out.mp4", work_dir=tmp_path)
    probe = media_binaries.ffprobe()
    if not probe.available:
        pytest.skip("没有 ffprobe")
    found = json.loads(
        subprocess.run(
            [probe.path, "-v", "error", "-select_streams", "v:0", "-count_frames",
             "-show_entries", "stream=nb_read_frames:format=duration", "-of", "json",
             str(joined)],
            capture_output=True, text=True,
        ).stdout
    )
    # Six one-second clips: 180 frames — which was never the part that broke.
    # The frames were all there, spread over 6.29 seconds instead of 6, each
    # one held a little longer than it should be. That is what puts a caption
    # a second and a half behind by the end of a film.
    assert int(found["streams"][0]["nb_read_frames"]) == 180
    assert float(found["format"]["duration"]) == pytest.approx(6.0, abs=0.02)
