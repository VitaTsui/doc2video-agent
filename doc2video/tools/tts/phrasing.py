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


# How long a clause may run before it is given somewhere to breathe. A clause
# with no punctuation in it is spoken in one unbroken push — 「它是全国石化化工
# 领域唯一一个国家级的AI应用中试平台」 is 25 characters of it — and what a person
# does there is take the small breaths the words allow.
LONG_CLAUSE_CHARS = 14
# What one of those breaths is worth. Small enough not to read as a pause, big
# enough to be a seam: the marks start at 0.24 for 、and this sits under it.
BREATH = 0.12
# And how much has to be said before the next one is allowed, so a run of short
# words does not turn into a stutter.
BREATH_EVERY = 3
# And how long one may run before it has to breathe wherever the dictionary
# lets it. Kept high on purpose: a forced break lands wherever jieba happened
# to split, and on a term it does not know — 「市经信局」 — that is inside a
# name. A phrase start is always the better seam when one is coming.
BREATH_LIMIT = 13

# Where a phrase starts: a numeral, an adverb, a pronoun, a conjunction, a
# preposition. These are what a person draws breath in front of.
_PHRASE_STARTS = ("m", "d", "r", "c", "p", "t")
# And what a phrase never starts with.
_PARTICLES = ("u", "y", "e", "o")
_BINDING = frozenset({"的", "地", "得", "和", "与", "及", "或", "是", "在", "把", "被", "了"})


def breaths(text: str) -> list[str]:
    """Cut a long clause into the pieces a person would breathe between.

    Word boundaries from the same dictionary that finds a word cut in half, and
    the seam goes where a phrase starts rather than wherever the count runs
    out: before a numeral, an adverb, a pronoun, a conjunction, a preposition.
    「它是 / 全国石化化工领域 / 唯一 / 一个国家级的 / AI应用中试平台」 — cutting by
    length alone gave 「它是全国石化 / 化工领域唯一」, which splits a name and
    starts a breath on a particle.

    Short clauses are left whole. The breath is for the ones that run on.
    """
    if len(text.strip()) <= LONG_CLAUSE_CHARS:
        return [text]
    tagged = _tagged(text)
    if not tagged:
        return [text]

    pieces: list[str] = []
    current = ""
    for index, (word, _flag) in enumerate(tagged):
        nxt = tagged[index + 1] if index + 1 < len(tagged) else None
        current += word
        if len(current) < BREATH_EVERY or nxt is None:
            continue
        next_word, next_flag = nxt
        if next_flag.startswith(_PARTICLES) or next_word in _BINDING:
            continue  # a breath never starts on 「的」
        if next_flag.startswith(_PHRASE_STARTS) or len(current) >= BREATH_LIMIT:
            pieces.append(current)
            current = ""
    if current:
        if pieces and len(current) < 3:
            pieces[-1] += current
        else:
            pieces.append(current)
    return pieces or [text]


def _tagged(text: str) -> list[tuple[str, str]]:
    """`(word, part of speech)` for each word, or `[]` with no tokenizer."""
    jieba = _tokenizer()
    if jieba is None:
        return []
    try:
        import jieba.posseg as posseg
    except ImportError:  # pragma: no cover - depends on the install
        return [(word, "x") for word, _s, _e in words(text)]
    return [(pair.word, pair.flag) for pair in posseg.cut(text)]
