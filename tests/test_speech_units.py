"""How a page is broken up before it is spoken, and where the beats fall.

A page handed to a synthesiser in one piece comes back in one voice: an average
pace found in the first sentence and held for forty seconds, with the same
pause at every mark. Spoken in units, the gaps between them are ours — and they
are the only place a machine reading can be given emphasis without asking the
engine for a feature it does not have.
"""

from __future__ import annotations

import wave
from pathlib import Path

from doc2video.tools.tts.base import join_units
from doc2video.tools.tts.units import (
    MAX_UNIT_SECONDS,
    PAUSE_EMPHASIS,
    PAUSE_SENTENCE,
    PAUSE_TURN,
    plan_units,
)


def test_a_page_is_spoken_in_several_units_not_one():
    """One utterance per page is where the flat delivery comes from."""
    sentences = [f"这是第 {i} 句话，长度大致相当，用来把一页填满。" for i in range(12)]
    units = plan_units(sentences)

    assert len(units) > 1
    for unit in units:
        assert unit.texts


def test_no_unit_runs_past_the_ceiling():
    """A long unit is a long stretch of unbroken machine, which is the problem."""
    from doc2video.tools.tts.base import estimate_duration

    sentences = ["很长很长的一句话，" * 6] * 6
    for unit in plan_units(sentences):
        # One sentence can exceed it on its own; two must not.
        if len(unit.texts) > 1:
            assert estimate_duration(unit.text) <= MAX_UNIT_SECONDS * 1.2


def test_the_emphasised_sentence_gets_the_longest_beat():
    """The writer already marked which sentence is the point of the page.

    Sized against the engine rather than in the abstract: `say` pauses about
    0.4s at its own punctuation, so a beat has to clear that to be heard as
    one. The first version added 0.42 and came out indistinguishable from an
    ordinary comma.
    """
    sentences = ["第一句在前面铺垫。", "第二句继续说明这件事情。", "这一句才是重点。"]
    units = plan_units(sentences, emphasis=[False, False, True])

    beats = [unit.pause_before for unit in units[1:]]
    assert PAUSE_EMPHASIS in beats
    assert PAUSE_EMPHASIS > PAUSE_TURN > PAUSE_SENTENCE


def test_a_turn_in_the_script_gets_its_own_beat():
    sentences = ["先讲清楚现在的做法。", "但是这样有个代价，得说明白。"]
    units = plan_units(sentences, emphasis=[False, False], weight=lambda _: 6.0)
    assert units[-1].pause_before == PAUSE_TURN


def test_joining_units_gives_exact_windows(tmp_path: Path):
    """The reason timing stops being a guess: the silence is written here.

    Where one unit stops and the next starts is arithmetic on sample counts,
    not something measured off the finished clip afterwards.
    """
    rate = 8000

    def tone(path: Path, seconds: float) -> None:
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(b"\x01\x00" * int(seconds * rate))

    first, second = tmp_path / "a.wav", tmp_path / "b.wav"
    tone(first, 1.0)
    tone(second, 2.0)

    windows = join_units([first, second], [0.0, 0.5], tmp_path / "joined.wav")

    assert windows[0] == (0.0, 1.0)
    assert windows[1] == (1.5, 3.5)
    with wave.open(str(tmp_path / "joined.wav"), "rb") as handle:
        assert handle.getnframes() == int(3.5 * rate)
