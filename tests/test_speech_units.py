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
    PAUSE_COLON,
    PAUSE_EMPHASIS,
    PAUSE_EXCLAIM,
    PAUSE_SEMICOLON,
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


def test_the_mark_says_how_long_the_pause_is():
    """A written line already says where it breathes; the marks are the answer.

    ；holds two halves of one thought together and ：leans into what follows,
    so neither may pause as long as the full stop that ends the thought.
    """
    sentences = ["先看结论。", "分成两半：", "一半是量，一半是质；", "再看代价。"]
    beats = [unit.pause_before for unit in plan_units(sentences)]

    assert beats[0] == 0.0  # the scene's own lead silence is already there
    assert beats[1] == PAUSE_SENTENCE
    assert beats[2] == PAUSE_COLON
    assert beats[3] == PAUSE_SEMICOLON
    assert PAUSE_COLON < PAUSE_SEMICOLON < PAUSE_SENTENCE < PAUSE_EXCLAIM


def test_a_closing_quote_does_not_hide_the_mark():
    """「他说：『走。』」 ends on a full stop, whatever its last character is."""
    beats = [unit.pause_before for unit in plan_units(["他说：「走。」", "然后就走了。"])]
    assert beats[1] == PAUSE_SENTENCE


def test_a_line_with_no_mark_at_all_still_gets_a_beat():
    """Titles and fragments arrive unpunctuated; silence is not the answer."""
    lines = ["石化AI商业情报中心", "先说这个平台的分量。"]
    beats = [unit.pause_before for unit in plan_units(lines)]
    assert beats[1] == PAUSE_SENTENCE


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
    """A mark decides where; 「但是」 decides that this one is worth longer."""
    sentences = ["先讲清楚现在的做法。", "但是这样有个代价，得说明白。"]
    units = plan_units(sentences, emphasis=[False, False])
    assert units[-1].pause_before == PAUSE_TURN


def test_a_turn_never_invents_a_pause_where_the_writing_has_none():
    """It lengthens the mark's beat. It is not a mark of its own.

    A comma inside a sentence stays inside the unit even when the clause after
    it turns — cutting there is the fault the punctuation rule exists to end:
    a pause in the middle of a sentence nobody wrote a break into.
    """
    units = plan_units(["现在的做法是这样，不过这样有个代价。"])
    assert len(units) == 1


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

    # Including the short ones. 「先说背景。」 is a full stop like any other, and
    # the length of what precedes a mark is not the mark's business — that test
    # (「上一句不足 2.2 秒就并进来」) was the last piece of clock in here.
    units = plan_units(["先说背景。", "这个揭榜为什么来，我们凭什么接，都在这一部分。"])
    assert len(units) == 2
    assert units[1].pause_before == PAUSE_SENTENCE


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
