"""Geometry and subtitle layout — where a wrong number means a wrong zoom."""

from __future__ import annotations

from doc2video.schemas import BBox, DocumentPage, NarrationSegment, Scene
from doc2video.skills.layout import build_subtitles, to_frame_area, with_highlight_padding


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


def test_a_highlight_gets_the_same_air_on_every_side():
    """It is drawn, so its padding has to look even.

    The box used to be grown by 8% of each dimension separately, which on a
    wide line of text is 16 pixels at the sides and 2 above — loose where it
    should be tight and tight where it should breathe. Measured off a real
    frame: the outline sat 16px clear of the text horizontally and cut through
    the glyphs vertically.
    """
    area = BBox(x=0.2, y=0.5, w=0.1, h=0.02)
    padded = with_highlight_padding(area, 1920, 1080)

    left = (area.x - padded.x) * 1920
    top = (area.y - padded.y) * 1080
    assert round(left, 1) == round(top, 1) == 7.0

    # And the same on the far sides.
    right = (padded.x + padded.w - area.x - area.w) * 1920
    bottom = (padded.y + padded.h - area.y - area.h) * 1080
    assert round(right, 1) == round(bottom, 1) == 7.0


def test_a_highlight_near_the_bottom_keeps_its_height():
    """It marks an element; a box that has been cut marks nothing.

    The old rule trimmed a highlight that reached into the subtitle band, so
    on a real deck the outline stopped halfway down the line it was pointing
    at. An outline overlapped by a subtitle for a few seconds is a smaller
    problem than one that appears to be around the wrong thing.
    """
    low = BBox(x=0.2, y=0.86, w=0.1, h=0.024)
    padded = with_highlight_padding(low, 1920, 1080)
    assert padded.h > low.h
    assert round((padded.y + padded.h) * 1080) == round((low.y + low.h) * 1080 + 7)


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
