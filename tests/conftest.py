"""Shared fixtures. Every test runs against an isolated storage directory."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from doc2video.core.config import Settings  # noqa: E402
from doc2video.storage import ProjectStore  # noqa: E402

from make_demo import SLIDES as DEMO_SLIDES  # noqa: E402
from make_demo import build as build_demo  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # Force the offline path so tests never depend on a network call.
    return Settings(
        storage_dir=tmp_path / "storage",
        llm_provider="mock",
        tts_provider="mock",
        video_width=1280,
        video_height=720,
        video_fps=24,
    )


@pytest.fixture
def store(settings: Settings) -> ProjectStore:
    settings.ensure_dirs()
    return ProjectStore(settings)


# Tests assert against the demo deck; derive its size so the two cannot drift.
DEMO_PAGE_COUNT = len(DEMO_SLIDES)


@pytest.fixture
def demo_pptx(tmp_path: Path) -> Path:
    path = tmp_path / "demo.pptx"
    build_demo(path)
    return path
