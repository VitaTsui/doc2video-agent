"""Schema behaviour that the incremental-render guarantee depends on."""

from __future__ import annotations

from doc2video.schemas import (
    BBox,
    DirectorAction,
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


def test_content_hash_is_stable_for_unchanged_scene():
    scene = Scene(scene_id="scene_01", narration="你好", duration=3.0)
    assert scene.content_hash() == scene.content_hash()


def test_content_hash_changes_when_narration_changes():
    scene = Scene(scene_id="scene_01", narration="你好", duration=3.0)
    before = scene.content_hash()
    scene.narration = "你好，世界"
    assert scene.content_hash() != before


def test_content_hash_changes_when_actions_change():
    scene = Scene(scene_id="scene_01", narration="你好", duration=3.0)
    before = scene.content_hash()
    scene.actions.append(DirectorAction(at=1.0, type="zoom", target="p01_e01", duration=2.0))
    assert scene.content_hash() != before


def test_dirty_scenes_only_lists_changed_ones():
    project = _project()
    for scene in project.scenes:
        project.render.rendered_scenes[scene.scene_id] = scene.content_hash()
    assert project.dirty_scenes() == []

    project.scenes[1].narration = "改过的第二页"
    dirty = project.dirty_scenes()
    assert [s.scene_id for s in dirty] == ["scene_02"]


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
