"""The quality score — what makes two runs comparable.

The score has to move for the reasons a viewer would care about, and stay put
for the ones they wouldn't. In particular it must not drift with deck length:
a 40-page deck with two broken scenes is in better shape than a 4-page deck
with two, and a score that just counted findings would say the opposite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doc2video.core.config import Settings
from doc2video.schemas import (
    ActionType,
    BBox,
    DirectorAction,
    DocumentModel,
    DocumentPage,
    ElementKind,
    Scene,
    SceneAudio,
    SceneVisual,
    SlideElement,
    Source,
    SourceType,
    VideoIntent,
    VideoProject,
)
from doc2video.skills.base import SkillContext
from doc2video.skills.review import ReviewSkill
from doc2video.storage import ProjectStore


def _scene(index: int, *, actions: bool = True, narration: str = "这一页讲的是系统架构。") -> Scene:
    return Scene(
        scene_id=f"scene_{index:02d}",
        source_page=index,
        title=f"第 {index} 页",
        narration=narration,
        duration=20.0,
        visual=SceneVisual(asset=f"assets/page_{index:03d}.png"),
        audio=SceneAudio(path=f"audio/scene_{index:02d}.wav", duration=20.0),
        actions=(
            [
                DirectorAction(
                    at=2.0, type=ActionType.HIGHLIGHT, target=f"p{index}_e1", duration=3.0
                )
            ]
            if actions
            else []
        ),
    )


def _page(index: int) -> DocumentPage:
    return DocumentPage(
        index=index,
        title=f"第 {index} 页",
        elements=[
            SlideElement(
                id=f"p{index}_e1",
                kind=ElementKind.PARAGRAPH,
                text="系统架构说明",
                bbox=BBox(x=0, y=0, w=100, h=100),
            )
        ],
    )


def _project(scenes: list[Scene], *, duration: int = 100) -> VideoProject:
    return VideoProject(
        project_id="proj_quality",
        source=Source(type=SourceType.PPTX, file="d.pptx", path="source/d.pptx"),
        intent=VideoIntent(duration=duration),
        document=DocumentModel(pages=[_page(s.source_page) for s in scenes]),
        scenes=scenes,
    )


@pytest.fixture
def score(settings: Settings, store: ProjectStore, tmp_path: Path):
    """Score a project with its assets actually on disk, as a real run would."""

    def _score(project: VideoProject):
        base = store.ensure_layout(project.project_id)
        for scene in project.scenes:
            if scene.visual.asset:
                path = base / scene.visual.asset
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"\x89PNG\r\n\x1a\n")
            if scene.audio.path:
                path = base / scene.audio.path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"RIFF")
        ctx = SkillContext.build(project, store=store, settings=settings)
        ReviewSkill(ctx).run()
        return project.quality

    return _score


def test_a_clean_project_scores_high(score):
    report = score(_project([_scene(i) for i in range(1, 6)]))

    assert report.score > 90
    assert report.errors == 0


def test_a_missing_audio_track_costs_completeness(score):
    scenes = [_scene(i) for i in range(1, 6)]
    scenes[0].audio.path = ""

    report = score(_project(scenes))

    completeness = next(d for d in report.dimensions if d.name == "completeness")
    assert completeness.score < 100
    assert report.errors >= 1


def test_the_score_does_not_drift_with_deck_length(score):
    """Two broken scenes out of forty is not the same as two out of four."""
    small = [_scene(i) for i in range(1, 5)]
    large = [_scene(i) for i in range(1, 41)]
    for scenes in (small, large):
        scenes[0].audio.path = ""
        scenes[1].audio.path = ""

    small_score = score(_project(small, duration=80)).score
    large_score = score(_project(large, duration=800)).score

    assert large_score > small_score


def test_read_aloud_narration_costs_originality(score):
    scenes = [_scene(i, narration="系统架构说明") for i in range(1, 6)]

    report = score(_project(scenes))

    originality = next(d for d in report.dimensions if d.name == "originality")
    assert originality.score < 100


def test_a_deck_with_no_camera_work_scores_lower_on_direction(score):
    with_actions = score(_project([_scene(i) for i in range(1, 6)]))
    without = score(_project([_scene(i, actions=False) for i in range(1, 6)]))

    assert without.score < with_actions.score
    direction = next(d for d in without.dimensions if d.name == "direction")
    assert direction.score == 0


def test_missing_the_target_duration_costs_pacing(score):
    scenes = [_scene(i) for i in range(1, 6)]  # 100s of video

    on_target = score(_project(scenes, duration=100))
    way_off = score(_project(scenes, duration=400))

    assert way_off.score < on_target.score


def test_dimensions_carry_their_own_evidence(score):
    report = score(_project([_scene(i) for i in range(1, 6)]))

    assert {d.name for d in report.dimensions} == {
        "completeness",
        "pacing",
        "originality",
        "direction",
        "subtitles",
    }
    assert all(d.detail for d in report.dimensions)
    assert abs(sum(d.weight for d in report.dimensions) - 1.0) < 1e-9
