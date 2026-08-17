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
