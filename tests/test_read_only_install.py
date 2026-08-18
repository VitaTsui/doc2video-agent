"""Nothing may be written into the program directory.

In a source checkout the Node workspace is writable and it did not matter that
the renderers staged assets inside it. Installed — /Applications, Program Files
— that directory is read-only, and a render that scribbles there cannot run at
all. These pin the boundary: the Node workspace is an input, storage is the
only output.
"""

from __future__ import annotations

from pathlib import Path

from doc2video.core.config import Settings
from doc2video.tools.renderer import renderer_status, select_adapter
from doc2video.tools.renderer.remotion import RemotionAdapter
from doc2video.tools.slides.chromium import ChromiumSlideRenderer


def _settings(tmp_path: Path) -> Settings:
    return Settings(storage_dir=tmp_path / "store", node_dir=tmp_path / "node")


def test_the_scene_renderer_stages_into_storage(tmp_path: Path):
    adapter = RemotionAdapter(_settings(tmp_path))
    assert (tmp_path / "store") in adapter.public_dir.parents
    assert (tmp_path / "node") not in adapter.public_dir.parents


def test_the_slide_renderer_stages_into_storage(tmp_path: Path):
    renderer = ChromiumSlideRenderer(_settings(tmp_path))
    assert (tmp_path / "store") in renderer.work_root.parents
    assert renderer.renderer_dir == tmp_path / "node"


def test_selecting_an_adapter_hands_it_the_caller_s_settings(tmp_path: Path):
    """The gap this closes: adapters used to be built with no arguments and
    silently read the process-wide singleton, so a render launched with one
    storage directory wrote its scratch into another."""
    settings = _settings(tmp_path)
    settings.renderer = "ffmpeg"
    adapter = select_adapter(settings)
    assert adapter.settings is settings


def test_status_reports_against_the_given_settings(tmp_path: Path):
    status = renderer_status(_settings(tmp_path))
    assert status["remotion"]["available"] is False
    # The reason names the directory that was actually looked in.
    assert str(tmp_path / "node") in status["remotion"]["reason"]
