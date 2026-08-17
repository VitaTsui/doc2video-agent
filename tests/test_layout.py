"""Geometry and subtitle layout — where a wrong number means a wrong zoom."""

from __future__ import annotations

from doc2video.schemas import BBox, DocumentPage, NarrationSegment, Scene
from doc2video.skills.layout import avoids_subtitle_band, build_subtitles, to_frame_area


def test_to_frame_area_accounts_for_letterboxing():
    # A 4:3 page inside a 16:9 frame: scaled to full height, padded left/right.
    page = DocumentPage(index=1, width=1200, height=900)
    area = to_frame_area(BBox(x=0, y=0, w=1200, h=900), page, 1600, 900)

    assert area.h == 1.0
    assert round(area.w, 4) == 0.75
    # Padding is split evenly, so the page starts an eighth of the way in.
    assert round(area.x, 4) == 0.125


def test_to_frame_area_maps_center_element():
    page = DocumentPage(index=1, width=1920, height=1080)
    area = to_frame_area(BBox(x=960, y=540, w=192, h=108), page, 1920, 1080)
    assert round(area.x, 3) == 0.5
    assert round(area.w, 3) == 0.1


def test_to_frame_area_clamps_out_of_bounds():
    page = DocumentPage(index=1, width=1000, height=1000)
    area = to_frame_area(BBox(x=-200, y=-200, w=400, h=400), page, 1000, 1000)
    assert area.x >= 0 and area.y >= 0
    assert area.x + area.w <= 1.0


def test_avoids_subtitle_band_trims_only_when_needed():
    high = BBox(x=0.1, y=0.1, w=0.3, h=0.2)
    assert avoids_subtitle_band(high) == high

    low = BBox(x=0.1, y=0.8, w=0.3, h=0.2)
    trimmed = avoids_subtitle_band(low)
    assert trimmed.y + trimmed.h <= 0.881


def test_build_subtitles_stays_inside_segment_window():
    scene = Scene(
        scene_id="scene_01",
        narration="这是第一句话，它比较长需要拆分成多条字幕。这是第二句。",
        segments=[
            NarrationSegment(
                id="s1", text="这是第一句话，它比较长需要拆分成多条字幕。", start=0.0, end=6.0
            ),
            NarrationSegment(id="s2", text="这是第二句。", start=6.0, end=8.0),
        ],
        duration=8.0,
    )
    cues = build_subtitles(scene)

    assert len(cues) >= 2
    assert cues[0].start == 0.0
    assert all(cue.end <= 8.0 for cue in cues)
    assert all(cue.start < cue.end for cue in cues)
    assert all(len(cue.text) <= 32 for cue in cues)
    # The comma is a cut, not a character: the first clause ends on its own.
    assert cues[0].text == "这是第一句话"
    assert not any(c in cue.text for cue in cues for c in "，。！？；、")
    # Every clause survives its segment's window — none is pushed past the end.
    assert [cue.text for cue in cues] == [
        "这是第一句话",
        "它比较长需要拆分成多条字幕",
        "这是第二句",
    ]


def test_short_clauses_all_fit_their_segment():
    """Many small clauses in one window: the floor must not evict the tail."""
    scene = Scene(
        scene_id="scene_01",
        narration="检索、生成、编程、推理、协同，都要靠它。",
        segments=[
            NarrationSegment(
                id="s1", text="检索、生成、编程、推理、协同，都要靠它。", start=0.0, end=3.0
            )
        ],
        duration=3.0,
    )
    cues = build_subtitles(scene)

    assert [cue.text for cue in cues] == ["检索", "生成", "编程", "推理", "协同", "都要靠它"]
    assert cues[-1].end <= 3.0
    assert all(cue.start < cue.end for cue in cues)
