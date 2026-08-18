"""Speech and subtitles on the platforms that had neither.

Two silent failures kept this project macOS-only, and both looked like success:
a run off macOS produced a correctly timed, correctly subtitled, *mute* video,
and a run on Windows dropped its burned-in subtitles with a warning nobody
reads. Neither raised anything.
"""

from __future__ import annotations

from pathlib import Path

from doc2video.core.config import Settings
from doc2video.tools.parsers.slide_raster import FONT_CANDIDATES, font_candidates
from doc2video.tools.tts.piper import PiperProvider
from doc2video.tools.tts.providers import AUTO_ORDER, resolve_provider


def test_every_platform_has_a_font_to_fall_back_on():
    """The list is read twice — by the rasteriser and by the subtitle burner —
    so a platform missing from it loses its subtitles without saying so."""
    joined = " ".join(FONT_CANDIDATES)
    assert "/System/Library/Fonts" in joined, "macOS"
    assert "C:/Windows/Fonts" in joined, "Windows"
    assert "/usr/share/fonts" in joined, "Linux"


def test_a_bundled_font_outranks_whatever_the_machine_has(tmp_path: Path, monkeypatch):
    """A packaged app cannot assume the machine has any CJK font at all."""
    from doc2video.core import config

    fonts = tmp_path / "fonts"
    fonts.mkdir()
    (fonts / "NotoSansSC.otf").write_bytes(b"not really a font")
    monkeypatch.setattr(
        config, "get_settings", lambda: Settings(node_dir=tmp_path / "node")
    )

    candidates = font_candidates()
    assert candidates[0].endswith("NotoSansSC.otf")
    assert len(candidates) == len(FONT_CANDIDATES) + 1


def test_piper_is_in_the_auto_order_after_say():
    """`say` is instant and needs no model; piper is what everyone else gets."""
    names = [cls.name for cls in AUTO_ORDER]
    assert names.index("macos_say") < names.index("piper") < names.index("silent")


def test_piper_says_what_it_needs_rather_than_downloading_mid_render(tmp_path: Path):
    """61MB fetched during a render is indistinguishable from a hang."""
    provider = PiperProvider(Settings(storage_dir=tmp_path))
    if provider.available():  # a machine that already has a voice installed
        return
    reason = provider.unavailable_reason()
    assert "doc2video voices" in reason or "piper-tts" in reason


def test_an_unavailable_provider_never_leaves_the_pipeline_without_one():
    """Voicing must degrade to silence, not fail: the video is still watchable."""
    assert resolve_provider("piper").available()
    assert resolve_provider("nonexistent").available()


def test_the_cli_progress_printer_matches_what_the_pipeline_sends(capsys):
    """The CLI is the one caller the test suite never routes through, which is
    how it kept a two-argument callback after the pipeline grew to four and
    `doc2video run` began crashing at its first step."""
    import inspect

    from doc2video.agent.executor import Executor
    from doc2video.cli import _print_progress

    Executor(None)  # type: ignore[arg-type]  # just to touch the default
    _print_progress("render", "渲染场景 scene_01", 2, 9)
    assert "2/9" in capsys.readouterr().err

    # Every emission point passes four; the printer must accept four.
    assert len(inspect.signature(_print_progress).parameters) == 4


def test_output_streams_are_made_utf8_before_anything_prints(monkeypatch):
    """Windows consoles default to a legacy code page, and every message this
    project writes is in Chinese — so `doctor` did not print mangled text on
    Windows, it crashed on its first line."""
    import io
    import sys

    from doc2video.core.logging import use_utf8

    class Legacy(io.StringIO):
        encoding = "cp1252"
        reconfigured: dict = {}

        def reconfigure(self, **kwargs):
            Legacy.reconfigured = kwargs

    monkeypatch.setattr(sys, "stdout", Legacy())
    use_utf8()
    assert Legacy.reconfigured.get("encoding") == "utf-8"
    # Never raise on a stream that cannot be reconfigured: losing the message
    # is bad, taking the process down with it is worse.
    monkeypatch.setattr(sys, "stdout", object())
    use_utf8()
