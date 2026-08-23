"""Where Chinese words begin, and what to do when the engine disagrees.

A synthesiser picks its own phrase boundaries. On any term it has not met it
picks them by guess, and the guess can land inside a word: measured on
「国家人工智能应用中试基地」, macOS `say` stops 0.27 seconds after 中 — reading
「应用中」 as a phrase and stranding 「试基地」. Nothing in the script says to
pause there, and a listener hears a word cut in half.

Nothing about the text predicts which words an engine will get wrong, so this
does not try to. It listens instead: the clip is written, its silences are
measured, and a silence that falls *inside* a word is repaired by telling the
engine where that word starts (`TTSProvider.phrase_boundary`) and speaking the
line once more. One extra call, only for the lines that came out wrong.

Word boundaries come from jieba — a dictionary, so it knows 中试 is a word and
「应用中」 is not. When it cannot be imported the repair is simply off; every
other part of speaking a page is unaffected.
"""

from __future__ import annotations

import functools

from ...core.logging import get_logger

log = get_logger(__name__)

# A gap this long inside a clause is worth looking at. It used to be 0.22s,
# which was the right bar when a clip was a whole sentence and the estimate of
# where a gap fell drifted a couple of characters — below that, legitimate
# breaks got 「repaired」 into markers they did not need.
#
# A clip is one clause now, a dozen characters long, and the estimate lands
# within a character. What the old bar missed: 「一是供应链经营风险可控化」 broke
# after 一是供应 by 0.14 seconds — a third of a beat in the middle of 供应链, and
# the first thing after a full stop's pause, which is exactly where a listener
# is paying attention. Length is not the same thing as audibility; being inside
# a word is.
AUDIBLE_GAP = 0.10

# A gap beside one of these is the gap that character asks for, not a mistake.
MARKS = "，、；：。！？…—,;:.!?"

# Single characters are not worth guarding: a break beside one is a break at a
# word boundary, whichever side it lands on.
SHORTEST_WORD = 2

# How much longer than this clip's shortest ordinary break a gap has to be
# before it counts as the engine having lost its place.
OUTLIER = 1.3

# How far from the estimated position to look for a mark. The estimate drifts:
# measured on two real lines it sat 2 characters early on one and 2 late on the
# other, which is enough to read a comma's own pause as a word being cut in
# half — and 「repairing」 a comma is inserting a boundary where the engine was
# already doing the right thing.
NEAR = 3


@functools.lru_cache(maxsize=1)
def _tokenizer():
    try:
        import jieba
    except ImportError as exc:  # pragma: no cover - depends on the install
        log.info("没有 jieba，断词修复关闭（%s）", exc)
        return None
    return jieba


def words(text: str) -> list[tuple[str, int, int]]:
    """`(word, start, end)` for each word in `text`, or `[]` with no tokenizer."""
    jieba = _tokenizer()
    if jieba is None or not text.strip():
        return []
    return [(word, start, end) for word, start, end in jieba.tokenize(text)]


def word_broken_at(text: str, index: int) -> int | None:
    """The start of the word `index` falls inside, or None if it falls between words.

    Strictly inside: an index at a word's first or last boundary is a boundary
    the engine was entitled to use.
    """
    for _word, start, end in words(text):
        if end - start < SHORTEST_WORD:
            continue
        if start < index < end:
            return start
    return None


def guard(text: str, starts: set[int]) -> str:
    """Mark where the named words begin, so the engine cannot break inside them.

    A space is how the spoken form says 「a word starts here」; each engine
    renders it in its own terms and the caption never sees it.
    """
    for start in sorted(starts, reverse=True):
        if 0 < start < len(text) and not (text[start - 1].isspace() or text[start].isspace()):
            text = text[:start] + " " + text[start:]
    return text


def _at_mark(text: str, at: float, gaps: list[tuple[float, float]], voiced: float) -> bool:
    """Is this silence the one a punctuation mark asks for?"""
    before = at - sum(g for start, g in gaps if start < at)
    index = _char_at(text, before / voiced) + 1
    return any(ch in MARKS for ch in text[max(0, index - NEAR) : index + NEAR])


def _char_at(text: str, fraction: float) -> int:
    """Which character is being spoken `fraction` of the way through a line.

    Not `fraction * len(text)`: characters are not spoken at one rate, and the
    punctuation costs nothing to say. Dividing by count made the answer depend
    on whether the clip's trailing silence had been counted — the same clip,
    measured two ways, put the same gap two characters apart. The estimator the
    timing ladder already uses is the same model spent per character.
    """
    from .base import weight_of

    weights = [weight_of(ch) for ch in text]
    total = sum(weights) or 1.0
    target = max(0.0, min(1.0, fraction)) * total
    spent = 0.0
    for index, weight in enumerate(weights):
        spent += weight
        if spent >= target:
            return index
    return len(text) - 1


def split(text: str, starts: set[int]) -> list[str]:
    """Cut `text` where those words begin.

    The pieces are spoken separately and joined with no gap between them, which
    is what makes this cheaper than telling the engine where the boundary is:
    a marker inside one utterance makes `say` re-plan its prosody around it and
    the clause comes back 19% longer (2.51s → 2.98s), while the same clause cut
    in two and rejoined runs 2.47s — shorter than the original, because each
    piece's own quiet edges are trimmed before they meet.
    """
    cuts = sorted(start for start in starts if 0 < start < len(text))
    pieces, previous = [], 0
    for cut in cuts:
        pieces.append(text[previous:cut])
        previous = cut
    pieces.append(text[previous:])
    return [piece for piece in pieces if piece.strip()]


def repairs_for(text: str, gaps: list[tuple[float, float]], duration: float) -> set[int]:
    """Which words a clip's silences cut in half.

    Where a gap falls in the text is estimated, not known: characters are not
    spoken at a constant rate and nothing here aligns them. Two things keep the
    estimate honest enough to act on. Silence is taken out of the clock first —
    the gaps themselves and the clip's own quiet edges are time in which no
    character is spoken, and leaving them in shifted the answer by two
    characters on a six-second line. And the neighbours are checked too: a word
    is several characters wide, so 「around here」 finds it.

    Every candidate that lands inside a word is guarded, not just the first.
    Marking one word too many costs a 40-millisecond boundary at a place the
    engine could have broken anyway; missing the right one leaves the word cut
    in half, which is what someone complained about.
    """
    if duration <= 0 or not text:
        return set()

    silence = sum(length for _, length in gaps)
    voiced = duration - silence
    if voiced <= 0:
        return set()

    # An engine breaks where it thinks a phrase ends, and those breaks are
    # short. The one it takes because it mis-read a word is a full stop's worth
    # of silence in the middle of a line — on the measured clip, 0.27 seconds
    # against 0.18 for the two boundaries it got right. So the bar is relative
    # to this clip's own rhythm as well as absolute: the same engine at a
    # different rate, or a different engine entirely, keeps its own scale.
    inside = [length for at, length in gaps if not _at_mark(text, at, gaps, voiced)]

    starts: set[int] = set()
    for at, length in gaps:
        # Compared with the *other* breaks in this line, not with itself: a
        # line whose only internal break is the wrong one has nothing to be
        # unusual against, and would exclude itself from repair.
        others = [g for g in inside if g is not length] or [
            g for g in inside if g != length
        ][1:]
        floor = max(AUDIBLE_GAP, min(others, default=0.0) * OUTLIER)
        if length < floor:
            continue
        before = at - sum(g for start, g in gaps if start < at)
        # The character being spoken when the silence begins is the one before
        # the break; the boundary is after it.
        index = _char_at(text, before / voiced) + 1
        # A pause at a comma is the comma being read, and the words on either
        # side of it are not being cut in half.
        if any(ch in MARKS for ch in text[max(0, index - NEAR) : index + NEAR]):
            continue
        # Nor is a pause where a word begins. Sweeping the neighbours for one
        # would find the words on both sides of a perfectly good boundary and
        # guard them both — three markers on a line that needed one, and two
        # new gaps of 0.16s in the audio where the engine had been fluent.
        if index in {start for _, start, _ in words(text)}:
            continue
        for candidate in (index, index + 1, index - 1):
            if (start := word_broken_at(text, candidate)) is not None:
                starts.add(start)
                break
    return starts
