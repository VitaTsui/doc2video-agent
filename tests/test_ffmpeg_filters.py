"""Graceful degradation when an ffmpeg build lacks an optional filter.

Real case this guards: the vendored Linux ffmpeg has no ``drawtext``. Before
this check, that turned into "Filter not found" and killed the whole render;
now it costs subtitles and nothing else.
"""

from __future__ import annotations

import pytest

from doc2video.tools import media_binaries
from doc2video.tools.renderer.base import PlanSubtitle, ScenePlan
from doc2video.tools.renderer.ffmpeg_adapter import FFmpegAdapter


@pytest.fixture(autouse=True)
def _clear_cache():
    media_binaries.reset_cache()
    yield
    media_binaries.reset_cache()


def _plan() -> ScenePlan:
    return ScenePlan(
        scene_id="scene_01",
        duration=5.0,
        width=1920,
        height=1080,
        fps=30,
        image="/tmp/page.png",
        subtitles=[PlanSubtitle(start=0.0, end=2.0, text="一句字幕")],
    )


def test_subtitles_are_dropped_when_drawtext_is_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(media_binaries, "has_filter", lambda name: name != "drawtext")
    filters = FFmpegAdapter()._build_filters(_plan())

    assert not any(f.startswith("drawtext=") for f in filters)
    # The rest of the graph must survive intact.
    assert any(f.startswith("scale=") for f in filters)
    assert any(f.startswith("fade=") for f in filters)


def test_subtitles_are_emitted_when_drawtext_exists(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(media_binaries, "has_filter", lambda name: True)
    monkeypatch.setattr(
        "doc2video.tools.renderer.ffmpeg_adapter._find_font", lambda: "/tmp/font.ttc"
    )
    filters = FFmpegAdapter()._build_filters(_plan())

    assert any(f.startswith("drawtext=") for f in filters)


def test_caption_is_anchored_to_the_bottom_edge(monkeypatch: pytest.MonkeyPatch):
    """The gap below the box is the plan's, so both renderers agree on it.

    Anchoring to ``h-text_h`` rather than a fixed line is what makes that gap
    mean the same thing here as in the Remotion component, where it is CSS
    padding under a bottom-aligned box.
    """
    monkeypatch.setattr(media_binaries, "has_filter", lambda name: True)
    monkeypatch.setattr(
        "doc2video.tools.renderer.ffmpeg_adapter._find_font", lambda: "/tmp/font.ttc"
    )
    plan = _plan()
    plan.subtitle_margin = 0.05
    draw = next(f for f in FFmpegAdapter()._build_filters(plan) if f.startswith("drawtext="))

    # 0.05 * 1080 = 54px of frame, plus the border the box adds past the text.
    assert ":y=h-text_h-70:" in draw


def test_has_filter_reports_false_without_ffmpeg(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        media_binaries, "ffmpeg", lambda: media_binaries.Binary("ffmpeg", None, "missing")
    )
    media_binaries.has_filter.cache_clear()
    assert media_binaries.has_filter("drawtext") is False


@pytest.mark.skipif(not media_binaries.ffmpeg().available, reason="本机没有 ffmpeg")
def test_core_filters_exist_on_this_machine():
    # zoompan/drawbox/fade are load-bearing: without them the adapter has no
    # zoom, no highlight and no transition, and there is nothing to degrade to.
    for name in ("zoompan", "drawbox", "fade"):
        assert media_binaries.has_filter(name), f"当前 ffmpeg 缺少 {name} 滤镜"


def _render_band(tmp_path, text: str | None) -> bytes:
    """Render one scene for real and return the pixels of the subtitle band."""
    import subprocess

    from PIL import Image

    source = tmp_path / f"src-{'sub' if text else 'bare'}.png"
    Image.new("RGB", (640, 360), "white").save(source)

    plan = ScenePlan(
        scene_id="probe",
        duration=1.0,
        width=640,
        height=360,
        fps=12,
        image=str(source),
        subtitles=[PlanSubtitle(start=0.0, end=1.0, text=text)] if text else [],
    )
    clip = tmp_path / f"clip-{'sub' if text else 'bare'}.mp4"
    FFmpegAdapter().render_scene(plan, clip)

    frame = tmp_path / f"frame-{'sub' if text else 'bare'}.png"
    subprocess.run(
        [media_binaries.ffmpeg().path, "-y", "-loglevel", "error", "-i", str(clip),
         "-frames:v", "1", "-update", "1", str(frame)],
        check=True,
        capture_output=True,
    )
    band = Image.open(frame).convert("RGB").crop((0, 280, 640, 360))
    return band.tobytes()


@pytest.mark.skipif(not media_binaries.ffmpeg().available, reason="本机没有 ffmpeg")
def test_a_percent_sign_still_reaches_the_frame(tmp_path):
    """The one failure mode the graph-shape tests above cannot see.

    Escaping ``%`` as ``\\%`` produced a well-formed filtergraph, a zero exit
    code and a warning nobody reads — and no subtitle at all. Narration is full
    of percentages, so this is checked against real pixels.
    """
    from doc2video.tools.renderer.ffmpeg_adapter import _find_font

    if not media_binaries.has_filter("drawtext") or _find_font() is None:
        pytest.skip("本机 ffmpeg 无 drawtext 或没有可用字体")

    assert _render_band(tmp_path, "增长 333%") != _render_band(tmp_path, None)
