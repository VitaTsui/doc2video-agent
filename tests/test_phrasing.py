"""Repairing a word the synthesiser cut in half.

An engine picks its own phrase boundaries and picks them by guess on any term
it has not met. Measured on 「国家人工智能应用中试基地」 with macOS `say`: a
0.27-second silence after 中, reading 「应用中」 as a phrase and stranding
「试基地」. Long enough to hear, and nothing in the script asked for it.

Nothing about the text says which words an engine will mishandle, so the repair
listens rather than predicts: measure the clip, find silences that fall inside a
word, name where that word starts, say the line again.
"""

from __future__ import annotations

from doc2video.tools.tts import phrasing

LINE = "这是国家人工智能应用中试基地，制造领域石化化工方向的应用场景揭榜方案。"


def test_a_break_inside_a_word_is_found():
    """中试 is a word; 应用中 is not, which is the whole disagreement."""
    assert phrasing.word_broken_at(LINE, 11) == 10   # between 中 and 试
    assert phrasing.word_broken_at(LINE, 10) is None  # between 应用 and 中试


def test_a_pause_at_a_mark_is_the_mark_being_read():
    """The comma's own pause must not be repaired into a guarded word."""
    gaps = [(1.79, 0.27), (2.69, 0.41), (5.4, 0.18)]
    assert phrasing.repairs_for(LINE, gaps, 6.79) == {10}


def test_a_pause_where_a_word_begins_is_left_alone():
    """Sweeping the neighbours would guard the words on both sides of a good
    boundary — three markers on a line that needed one, and two new gaps in
    audio that had been fluent.

    The clip these numbers came from has three silences; the third lands on the
    应用｜场景 boundary and must contribute nothing.
    """
    gaps = [(1.79, 0.27), (2.69, 0.41), (5.4, 0.18)]
    assert phrasing.repairs_for(LINE, gaps, 6.79) == {10}
    # And the answer does not depend on whether the clip's own trailing silence
    # was part of the measurement.
    assert phrasing.repairs_for(LINE, gaps[:2], 6.79) == {10}


def test_a_gap_too_short_to_hear_is_not_a_defect():
    """Below the floor nothing is repaired — but the floor is low.

    It was 0.22s while a clip was a whole sentence and the estimate of where a
    gap fell drifted a couple of characters. A clip is one clause now, and the
    complaint that lowered it was a 0.14-second break in the middle of 供应链 —
    a third of a beat, and plainly audible because it was inside a word.
    """
    assert phrasing.repairs_for(LINE, [(1.79, 0.05)], 6.79) == set()
    assert phrasing.repairs_for("一是供应链经营风险可控化，", [(0.69, 0.14)], 2.51) == {2}


def test_the_guard_marks_where_the_word_starts():
    assert phrasing.guard(LINE, {10}).startswith("这是国家人工智能应用 中试基地")
    # Idempotent: a boundary that is already there is not doubled.
    once = phrasing.guard(LINE, {10})
    assert phrasing.guard(once, {10}) == once


def test_without_a_tokenizer_nothing_is_repaired(monkeypatch):
    """The dependency is a dictionary, not a load-bearing wall."""
    monkeypatch.setattr(phrasing, "_tokenizer", lambda: None)
    assert phrasing.words(LINE) == []
    assert phrasing.repairs_for(LINE, [(1.79, 0.27)], 6.79) == set()


def test_a_pause_two_characters_off_a_comma_is_still_the_comma():
    """The estimate drifts, and a 「repaired」 comma is a boundary the engine
    was already getting right.

    Measured on 「第三是项目建设主旨思路，第四是……」: both commas came back two
    characters late, so a window of one read them as words being cut in half
    and guarded 第四 and 最后.
    """
    line = "第三是项目建设主旨思路，第四是项目总体建设内容，最后是联合揭榜的商业价值。"
    gaps = [(1.35, 0.17), (2.26, 0.37), (4.66, 0.39)]
    assert phrasing.repairs_for(line, gaps, 7.16) == set()
