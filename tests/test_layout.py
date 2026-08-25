"""Geometry and subtitle layout — where a wrong number means a wrong zoom."""

from __future__ import annotations

import pytest

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
    assert all(len(cue.text) <= 28 for cue in cues)
    # The comma is a cut, not a character — and then the pieces are joined back
    # up to a line's worth, with a space where the comma was. Cutting at every
    # mark and stopping there gave captions of seven characters that were gone
    # in two seconds; a line holds a whole thought.
    assert not any(c in cue.text for cue in cues for c in "，。！？；")
    assert [cue.text for cue in cues] == [
        "这是第一句话 它比较长需要拆分成多条字幕",
        "这是第二句",
    ]


def test_a_list_stays_on_one_line():
    """`、` separates items inside a clause; a speaker does not stop there.

    Cutting at it produced four-character captions. The hand-written track
    this was measured against keeps every `、` and never cuts at one.
    """
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

    assert [cue.text for cue in cues] == ["检索、生成、编程、推理、协同 都要靠它"]
    assert cues[-1].end <= 3.0
    assert all(cue.start < cue.end for cue in cues)


def test_a_caption_is_cut_where_the_narrator_stops(tmp_path):
    """One line, and the voice audibly breaks in the middle of it.

    Clauses are joined up to a line's worth because a caption of seven
    characters is gone before it is read. Most joins are across a comma the
    voice runs through in a third of a second — but some are two thirds, and a
    line held across one of those reads as one continuous sentence while the
    narrator stops inside it.
    """
    import math
    import wave

    from doc2video.skills.layout import build_subtitles

    rate = 22050
    clip = tmp_path / "scene.wav"

    def tone(seconds):
        return b"".join(
            int(18000 * math.sin(i / 7)).to_bytes(2, "little", signed=True)
            for i in range(int(rate * seconds))
        )

    with wave.open(str(clip), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        # Two clauses with two thirds of a second of nothing between them.
        handle.writeframes(tone(2.0) + b"\x00\x00" * int(rate * 0.65) + tone(2.0))

    scene = Scene(
        scene_id="scene_01",
        narration="再谈痛点和思路，落到建设内容与商业价值。",
        segments=[
            NarrationSegment(
                id="s1", text="再谈痛点和思路，落到建设内容与商业价值。", start=0.0, end=4.65
            )
        ],
        duration=4.65,
    )

    # Without the clip there is nothing to know, and the line is one caption.
    assert len(build_subtitles(scene)) == 1

    cut = build_subtitles(scene, clip)
    assert [cue.text for cue in cut] == ["再谈痛点和思路", "落到建设内容与商业价值"]
    # And the cut lands in the silence, not at a guessed position.
    assert cut[0].end == pytest.approx(2.3, abs=0.25)


def test_a_page_is_listed_the_way_it_is_read():
    """A parser hands back what the file draws, and a slide is not drawn in order.

    Measured on one deck's page 8 — three columns headed 市场 / 经营 / 发展 left
    to right — the headings came back 经营, 发展, 市场, because that is the order
    PowerPoint laid them down; the bodies underneath came back the other way.
    Nothing downstream can recover from that: the writer is told to walk the
    page in order and walks it middle, right, left, and the camera follows the
    sentences across the slide and back.
    """
    from doc2video.schemas import BBox, ElementKind, SlideElement
    from doc2video.tools.parsers.reading_order import in_reading_order

    def at(name: str, x: float, y: float, w: float = 380) -> SlideElement:
        return SlideElement(
            id=name, kind=ElementKind.PARAGRAPH, text=name, bbox=BBox(x=x, y=y, w=w, h=40)
        )

    page = [
        at("标题", 204, 57, 1360),
        at("导语", 99, 187, 1729),
        # Drawn middle, right, left — which is the bug this fixes.
        at("经营", 723, 441),
        at("发展", 1304, 441),
        at("市场", 147, 441),
        at("市场一", 149, 530),
        at("经营一", 722, 530),
        at("发展一", 1307, 530),
        at("市场二", 149, 642),
        at("经营二", 722, 650),
        at("发展二", 1307, 650),
        at("结尾", 412, 943, 1072),
    ]

    read = [element.text for element in in_reading_order(page, 1920, 1080)]

    assert read[:2] == ["标题", "导语"], read
    assert read[-1] == "结尾", "横跨几栏的收尾句不属于任何一栏"
    # Column by column, not line by line across all three.
    assert read[2:5] == ["市场", "市场一", "市场二"], read
    assert read[5:8] == ["经营", "经营一", "经营二"], read
    assert read[8:11] == ["发展", "发展一", "发展二"], read


def test_a_single_column_page_is_simply_top_to_bottom():
    """The rule has to be invisible on the pages that never had the problem."""
    from doc2video.schemas import BBox, ElementKind, SlideElement
    from doc2video.tools.parsers.reading_order import in_reading_order

    page = [
        SlideElement(
            id=f"e{i}", kind=ElementKind.PARAGRAPH, text=f"第{i}行",
            bbox=BBox(x=120, y=100 + i * 90, w=1600, h=60),
        )
        for i in range(5)
    ]
    read = [element.text for element in in_reading_order(page, 1920, 1080)]
    assert read == [f"第{i}行" for i in range(5)]
