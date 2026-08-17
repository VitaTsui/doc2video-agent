"""Schema behaviour that the incremental-render guarantee depends on."""

from __future__ import annotations

from doc2video.schemas import (
    BBox,
    Scene,
    Source,
    SourceType,
    VideoProject,
)


def _project() -> VideoProject:
    return VideoProject(
        project_id="proj_test",
        source=Source(type=SourceType.PPTX, file="demo.pptx", path="source/demo.pptx"),
        scenes=[
            Scene(scene_id="scene_01", source_page=1, narration="第一页", duration=5.0),
            Scene(scene_id="scene_02", source_page=2, narration="第二页", duration=7.0),
        ],
    )


def test_total_duration_sums_scenes():
    assert _project().total_duration() == 12.0


def test_bbox_padding_and_normalization():
    box = BBox(x=100, y=50, w=200, h=100)
    padded = box.padded(0.1)
    assert padded.x == 80 and padded.w == 240

    normalized = box.normalized(1000, 500)
    assert normalized.x == 0.1
    assert normalized.h == 0.2
    assert box.center == (200.0, 100.0)
