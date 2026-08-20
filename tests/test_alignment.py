"""Where a sentence actually starts, and how much that differs from a guess.

The director points the camera at the moment a sentence begins. That moment
used to be inferred from how long the sentence is; on a real deck the inferred
boundary sat a median of 0.2s and a 95th-percentile 0.63s away from the pause
that actually separates the two sentences — far enough that a box can appear
after the narrator has moved on.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest

from doc2video.tools.tts import align
from doc2video.tools.tts.base import weight_of


def _clip(path: Path, spans: list[tuple[float, bool]], rate: int = 22050) -> None:
    """Write a WAV of alternating tone and silence, as `(seconds, is_speech)`."""
    frames = bytearray()
    for seconds, speaking in spans:
        for index in range(int(seconds * rate)):
            value = int(12000 * math.sin(index * 0.06)) if speaking else 0
            frames += struct.pack("<h", value)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))


@pytest.fixture
def two_sentences(tmp_path: Path) -> Path:
    # Speech, a clear half-second pause, speech. The boundary is at 3.25s.
    path = tmp_path / "clip.wav"
    _clip(path, [(3.0, True), (0.5, False), (3.0, True)])
    return path


def test_the_boundary_is_measured_rather_than_guessed(two_sentences: Path):
    """Two sentences of very different length: the guess lands far from the pause."""
    sentences = ["短句。", "这是一个明显长得多的句子，用来把比例估算拉偏。"]
    weights = [weight_of(s) for s in sentences]
    duration = 6.5

    measured = align.boundaries(two_sentences, sentences, duration, weights)
    assert measured is not None
    assert measured[0] == pytest.approx(3.25, abs=0.2)

    # The estimate puts it where the text says, which here is nowhere near.
    guessed = duration * weights[0] / sum(weights)
    assert abs(guessed - 3.25) > 0.8


def test_a_clip_with_no_pauses_keeps_the_estimate(tmp_path: Path):
    """Silence is not always there to be found, and inventing one is worse."""
    path = tmp_path / "flat.wav"
    _clip(path, [(4.0, True)])
    assert align.boundaries(path, ["一句。", "两句。"], 4.0, [1.0, 1.0]) is None


def test_fewer_gaps_than_sentences_falls_back_whole(tmp_path: Path):
    """Three sentences and one pause: measure none of them rather than one.

    Taking the single gap for one boundary and estimating the other leaves a
    clip whose cues are half measured and half guessed — harder to reason
    about than a consistent estimate, and much harder to debug when one lands
    in the wrong place.
    """
    path = tmp_path / "one-gap.wav"
    _clip(path, [(1.0, True), (0.4, False), (5.0, True)])
    sentences = ["一。", "二。", "三。"]
    assert align.boundaries(path, sentences, 6.4, [weight_of(s) for s in sentences]) is None


def test_boundaries_never_run_backwards(tmp_path: Path):
    """A sentence that ends before it starts breaks everything downstream."""
    path = tmp_path / "three.wav"
    _clip(path, [(1.0, True), (0.3, False), (1.0, True), (0.3, False), (3.0, True)])
    sentences = ["一。", "二。", "这一句要长得多，把估算整个拉偏。"]
    measured = align.boundaries(path, sentences, 5.6, [weight_of(s) for s in sentences])

    assert measured is not None
    assert measured == sorted(measured)
    assert len(measured) == 2


def test_the_timing_source_travels_with_the_result(tmp_path: Path, settings):
    """Three rungs that are not equally trustworthy, and one caller for all three."""
    from doc2video.tools.tts import TTSTool

    tool = TTSTool(settings)
    result = tool.synthesize("一句话。", tmp_path / "out.wav", sentences=["一句话。"])
    # A single sentence has no boundary to measure; the floor is the estimate.
    assert result.timing_source == "estimate"
    assert result.segments
