"""Cutting a clause so the engine's own silences disappear.

A clause has no punctuation inside it, so every silence in its clip is one
nobody asked for: the engine deciding where a phrase ends and getting it wrong
on any term it has not met. Measured with macOS `say` on 「国家人工智能应用中试
基地」 — 0.27 seconds after 中, reading 「应用中」 as a phrase and stranding
「试基地」; and on 「一是供应链经营风险可控化」 — 0.14 seconds after 一是供应, in
the middle of 供应链.

There is no threshold. A pause exists because a mark exists; a gap the text does
not call for is not made acceptable by being short. The clip is measured, cut at
the nearest word boundary to each silence, and the pieces are spoken separately
and rejoined with their quiet edges trimmed — so the gap is gone, not moved.
"""

from __future__ import annotations

from doc2video.tools.tts import phrasing

LINE = "这是国家人工智能应用中试基地"
CLAUSE = "一是供应链经营风险可控化，"


def test_a_break_inside_a_word_is_found():
    """中试 is a word; 应用中 is not, which is the whole disagreement."""
    assert phrasing.word_broken_at(LINE, 11) == 10   # between 中 and 试
    assert phrasing.word_broken_at(LINE, 10) is None  # between 应用 and 中试


def test_the_cut_goes_to_the_nearest_word_boundary():
    """0.14 seconds in the middle of 供应链, and 一是 is where the word starts."""
    assert phrasing.cuts_for(CLAUSE, [(0.68, 0.15), (2.44, 0.06)], 2.51) == {2}


def test_a_silence_is_cut_out_however_short_it_is():
    """The threshold is gone. 0.14s was audible; the rule is not about length.

    A pause belongs to a mark. Inside a clause there are no marks, so there is
    nothing for a silence to belong to.
    """
    assert phrasing.cuts_for(CLAUSE, [(0.68, 0.07)], 2.51) == {2}


def test_the_clip_s_own_quiet_edges_are_not_cuts():
    """Trimming removes those; cutting there would split off nothing."""
    assert phrasing.cuts_for(CLAUSE, [(0.0, 0.2), (2.45, 0.3)], 2.75) == set()


def test_splitting_keeps_every_character():
    pieces = phrasing.split(CLAUSE, {2})
    assert pieces == ["一是", "供应链经营风险可控化，"]
    assert "".join(pieces) == CLAUSE


def test_without_a_tokenizer_nothing_is_cut(monkeypatch):
    """The dependency is a dictionary, not a load-bearing wall."""
    monkeypatch.setattr(phrasing, "_tokenizer", lambda: None)
    assert phrasing.words(LINE) == []
    assert phrasing.cuts_for(CLAUSE, [(0.68, 0.15)], 2.51) == set()
