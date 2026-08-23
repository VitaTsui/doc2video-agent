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

# Single characters are not worth guarding: a break beside one is a break at a
# word boundary, whichever side it lands on.
SHORTEST_WORD = 2


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


def cuts_for(text: str, gaps: list[tuple[float, float]], duration: float) -> set[int]:
    """Where to cut this clip so the engine's own silences disappear.

    A clip is one clause and a clause has no punctuation inside it, so **every**
    silence in it is one nobody asked for. There is no threshold here any more:
    the rule is that a pause exists because a mark exists, and a gap the text
    does not call for is not made acceptable by being short — 0.14 seconds in
    the middle of 供应链 was the complaint that removed the last one.

    Cutting is what removes them. Each piece is spoken separately and the pieces
    are rejoined with their quiet edges trimmed off, so the gap is gone rather
    than moved. The cut goes to the nearest word boundary: inside a word, that
    is where the word starts; on a boundary already, that same boundary — the
    engine's guess about where the phrase ended is not why the silence has to
    go.
    """
    if duration <= 0 or not text:
        return set()

    silence = sum(length for _, length in gaps)
    voiced = duration - silence
    if voiced <= 0:
        return set()

    boundaries = sorted({start for _, start, _ in words(text)} | {len(text)})
    cuts: set[int] = set()
    for at, _length in gaps:
        before = at - sum(g for start, g in gaps if start < at)
        if before <= 0.01 or before >= voiced - 0.01:
            continue  # the clip's own quiet edges, which trimming removes
        # The character being spoken when the silence begins is the one before
        # the break; the boundary is after it.
        index = _char_at(text, before / voiced) + 1
        # Inside a word, the cut goes to where that word *starts* — that is
        # what stops the engine reaching into it again. The nearest boundary
        # would be the word's other end just as often, which leaves the word
        # whole in a piece that still begins where the engine likes to break.
        broken = word_broken_at(text, index)
        cut = broken if broken is not None else min(
            boundaries, key=lambda edge: (abs(edge - index), edge)
        )
        if 0 < cut < len(text):
            cuts.add(cut)
    return cuts
