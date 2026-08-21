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

import pytest

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


def test_where_a_unit_ends_is_a_question_about_the_language():
    """Not about the clock, which is what it used to be.

    Breaking whenever nine seconds had gone by put the long pause wherever the
    stopwatch landed. Measured on a thirty-page film: the gaps we chose ran a
    median of 1.44 seconds and the ones left to the engine ran 0.79 — so which
    sentence got a beat was decided by arithmetic on a duration estimate.
    """
    from doc2video.tools.tts.units import plan_units

    # Four ordinary sentences, none of them turning, none marked, and each one
    # long enough to stand on its own. The clock never enters into it.
    sentences = [
        "月活一万二，环比涨十八个点。",
        "复用率七十八，环比又加六个点。",
        "平均生成时长六点五分钟。",
        "从上传到成片，中间没有人工介入。",
    ]
    units = plan_units(sentences)
    assert len(units) == len(sentences)

    # A sentence too short to stand alone keeps the next one company rather
    # than being spoken between two breaths.
    units = plan_units(["先说背景。", "这个揭榜为什么来，我们凭什么接，都在这一部分。"])
    assert len(units) == 1


def test_the_beats_are_a_ladder_a_listener_can_hear():
    """Each level has to clear the one below it to mean anything.

    Levelled against what the engine leaves between two sentences it speaks in
    one breath — measured at 0.77s. A boundary we chose on purpose passing
    quicker than one nobody decided anything about is the rhythm inverted.
    """
    from doc2video.tools.tts import units as U

    assert U.PAUSE_SENTENCE >= 0.7
    assert U.PAUSE_TURN > U.PAUSE_SENTENCE
    assert U.PAUSE_EMPHASIS > U.PAUSE_TURN

    # And the page turn is the longest pause in the film: the viewer has a
    # whole new page to take in.
    from doc2video.core.config import get_settings

    settings = get_settings()
    assert settings.scene_lead_seconds + settings.scene_tail_seconds > U.PAUSE_EMPHASIS


def test_a_unit_is_joined_without_the_engine_s_own_silence(tmp_path: Path):
    """The gap between two units is the number asked for, and nothing else.

    Left on, a 「句号后停 0.10 秒」 measured 1.44 seconds on a real film — the
    pause we chose plus two clips' worth of edges.
    """
    import math
    import wave

    from doc2video.tools.tts.base import join_units

    rate = 22050

    def clip(path, before, speech, after):
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            loud = b"".join(
                int(18000 * math.sin(i / 7)).to_bytes(2, "little", signed=True)
                for i in range(int(rate * speech))
            )
            quiet = b"\x00\x00"
            handle.writeframes(
                quiet * int(rate * before) + loud + quiet * int(rate * after)
            )
        return path

    first = clip(tmp_path / "a.wav", 0.2, 1.0, 0.5)
    second = clip(tmp_path / "b.wav", 0.4, 1.0, 0.3)
    windows = join_units([first, second], [0.0, 0.75], tmp_path / "out.wav")

    # Speech, gap, speech — the gap between them is the 0.75 that was asked
    # for, not 0.75 plus the four tenths of nothing the clips came with.
    gap = windows[1][0] - windows[0][1]
    assert gap == pytest.approx(0.75, abs=0.05)
