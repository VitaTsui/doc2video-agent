"""Timestamp allocation — the input the director derives every cue from."""

from __future__ import annotations

from doc2video.tools.tts import allocate_segments, estimate_duration


def test_estimate_duration_scales_with_length():
    short = estimate_duration("你好世界")
    long = estimate_duration("你好世界" * 10)
    assert long > short > 0


def test_estimate_duration_respects_rate():
    assert estimate_duration("这是一段用于测试的中文文本", 2.0) < estimate_duration(
        "这是一段用于测试的中文文本", 1.0
    )


def test_allocate_segments_covers_whole_clip():
    sentences = ["第一句比较短。", "第二句明显要长很多，因此应该分到更多时间。", "第三句。"]
    segments = allocate_segments(sentences, 12.0)

    assert len(segments) == 3
    assert segments[0].start == 0.0
    assert segments[-1].end == 12.0
    # Contiguous: no gaps, no overlaps.
    for previous, current in zip(segments, segments[1:], strict=False):
        assert previous.end == current.start
    # Longer sentence gets a longer window.
    assert (segments[1].end - segments[1].start) > (segments[0].end - segments[0].start)


def test_allocate_segments_handles_empty_input():
    assert allocate_segments([], 10.0) == []
    assert allocate_segments(["有内容"], 0.0) == []
