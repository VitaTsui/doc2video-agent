"""Reading a character the way the sentence means it.

Chinese characters with more than one reading are the one thing a text-in /
audio-out engine cannot be told about. 「应用」 is *yìng*yòng and 「应该」 is
*yīng*gāi, and an engine that picks the character's commonest reading says the
first one wrong — measured on one 4824-character script: 79 places, 16
characters, and 「应」 alone was 29 of them on a deck about an 应用中试基地.

A hand-kept list of words was the first answer and does not scale: it fixes the
word that was heard going wrong and nothing else.

So the reading is worked out from the sentence — `pypinyin` carries a phrase
dictionary, which is what makes 「银行」 *háng* and 「行走」 *xíng* — and where
that reading differs from the one the character would get on its own, the
character is swapped for a common one that already reads that way. The engine
then says the right syllable because it is looking at a different character;
the caption keeps the original, because only what is spoken went through here.

Three rules, and the third was learned the hard way:

* **It does not touch a character whose contextual reading is its usual one.**
  That is the overwhelming majority, and every substitution is a chance to make
  a word that was right wrong.
* **It does not trust a rare substitute.** A character the engine may not know
  is worse than the wrong tone, so candidates are ranked by how common they are
  and anything below a floor is left alone.
* **It substitutes a whole word or none of it.** The engine resolves its own
  polyphones by recognising words, so swapping one character out of a word
  takes away the very thing the rest was relying on. 「银行行长」 came back with
  the 长 read *cháng*: 行 was swapped for a homophone, 「行长」 stopped being a
  word the engine knew, and the character it had been reading correctly went
  wrong. Whatever is substituted takes its whole word with it.
**What is not here, and why.** Most of these the engine already knows: on one
script's fourteen candidate words, nine came back as the same audio — 「应用」,
「行业」, 「参与」 — so rewriting them buys nothing. Asking the engine first
would be the obvious refinement, and it was tried: synthesise both spellings
and compare. It does not work. macOS `say` is not deterministic — the same four
characters came back as two different files inside one run — so bytes cannot be
compared, and the cheap tolerant comparisons (duration, loudness envelope) score
「供应」 against 「宫硬」 as identical because they only measure how loud it is,
not what was said. Telling two syllables apart needs a spectral comparison,
which is a different project.

So the dictionary is believed, always. It is right far more often than an
engine guessing at a character, and the cost of the times it is not is a word
spelled oddly to the engine and read correctly anyway.
"""

from __future__ import annotations

import functools
from collections.abc import Sequence

from ...core.logging import get_logger

log = get_logger(__name__)

#: How common a substitute has to be before it is worth using. jieba's own
#: frequency table, which is a word list — a character below this turns up so
#: rarely that an engine mispronouncing *it* is the likelier outcome.
MIN_SUBSTITUTE_FREQ = 300

#: Readings never worth substituting for. The neutral tone is what an engine
#: does with an unstressed syllable anyway, and 「的」「了」「着」 carry it.
_TONELESS = frozenset({"de", "le", "zhe", "me", "ne", "ba", "a"})

#: Characters whose reading changes by *rule* rather than by meaning. 「一」 is
#: yī, yí or yì depending only on what follows it, and 「不」 the same — every
#: engine applies the rule itself. Substituting them writes one instance of the
#: rule into the text and breaks it everywhere else: 「它是唯一一个」 came back as
#: 「它是唯一宜各」.
_SANDHI = frozenset("一不")


@functools.lru_cache(maxsize=1)
def _tables() -> tuple[dict[str, str], dict[str, list[str]]] | None:
    """`(default reading per character, common characters per reading)`.

    Built once from the pinyin dictionary and jieba's frequencies. Returns None
    when either is missing, and the caller leaves the text alone.
    """
    try:
        import jieba
        from pypinyin import Style, pinyin
        from pypinyin.constants import PINYIN_DICT
    except ImportError:  # pragma: no cover - both are dependencies
        log.debug("没有 pypinyin/jieba，多音字按引擎自己的读法")
        return None

    jieba.initialize()
    freq = jieba.dt.FREQ
    default: dict[str, str] = {}
    by_reading: dict[str, list[str]] = {}
    for code in PINYIN_DICT:
        char = chr(code)
        if not ("一" <= char <= "鿿"):
            continue
        readings = pinyin(char, heteronym=True, style=Style.TONE3)[0]
        if not readings:
            continue
        # The reading this character gets on its own — which is what an engine
        # with no phrase knowledge will say.
        default[char] = readings[0]
        # A stand-in whose own reading moves is no stand-in at all. 「不」 is the
        # commonest character reading bù, so it was chosen for 部 — and 「不份」
        # is *bú*fèn, because 不 drops to second tone before a fourth-tone
        # syllable. The rule that made 一 and 不 unsafe to replace makes them
        # unsafe to replace *with*.
        if char not in _SANDHI and freq.get(char, 0) >= MIN_SUBSTITUTE_FREQ:
            by_reading.setdefault(readings[0], []).append(char)
    for chars in by_reading.values():
        chars.sort(key=lambda c: -freq.get(c, 0))
    return default, by_reading


def for_reading(text: str, keep: Sequence[str] = ()) -> str:
    """`text` with the characters an engine would misread swapped for homophones.

    @param keep: spellings someone asked for by hand. A registered term has
        already said how it wants to be read, and a general rule about one of
        its characters is not entitled to a second opinion.
    """
    tables = _tables()
    if tables is None or not text.strip():
        return text
    default, by_reading = tables

    try:
        from pypinyin import Style, lazy_pinyin
    except ImportError:  # pragma: no cover
        return text

    readings = lazy_pinyin(text, style=Style.TONE3)
    # One reading per character is the only case this can act on: the
    # tokeniser folds runs of Latin and digits, and a mismatch means the
    # alignment is not character-for-character.
    if len(readings) != len(text):
        return text

    def stand_in(char: str, want: str) -> str | None:
        """A common character that already reads `want`, or None."""
        return next((c for c in by_reading.get(want, ()) if c != char), None)

    spared = _spans(text, keep)
    out: list[str] = []
    for start, word in _words(text):
        if any(start < end and start + len(word) > begin for begin, end in spared):
            out.append(word)
            continue
        chars = list(word)
        wants = readings[start : start + len(word)]
        # Which of this word's characters would be misread on their own.
        wrong = [
            index
            for index, (char, want) in enumerate(zip(chars, wants, strict=True))
            if _misread(char, want, default)
        ]
        if not wrong or any(stand_in(chars[i], wants[i]) is None for i in wrong):
            out.append(word)
            continue
        # The word is being taken apart, so every polyphone in it has to be
        # spelled out — the ones that were right only because the engine knew
        # the word are about to stop being right.
        for index, (char, want) in enumerate(zip(chars, wants, strict=True)):
            if index in wrong:
                chars[index] = stand_in(char, want) or char
            elif char not in _SANDHI and _ambiguous(char):
                chars[index] = stand_in(char, want) or char
        rewritten = "".join(chars)
        log.debug("多音字：%s → %s", word, rewritten)
        out.append(rewritten)
    return "".join(out)


def _spans(text: str, keep: Sequence[str]) -> list[tuple[int, int]]:
    """Where in `text` the hand-registered spellings sit."""
    found: list[tuple[int, int]] = []
    for phrase in keep:
        if not phrase:
            continue
        at = text.find(phrase)
        while at >= 0:
            found.append((at, at + len(phrase)))
            at = text.find(phrase, at + 1)
    return found


def _misread(char: str, want: str, default: dict[str, str]) -> bool:
    """Would this character be read another way on its own?

    Not for a character whose reading follows a rule the engine already applies
    — tone sandhi, and the destressing that turns a full syllable into a
    neutral one. Both are the engine being right, and writing them into the
    text makes them wrong the next time round.
    """
    usual = default.get(char)
    if usual is None or usual == want or char in _SANDHI or want in _TONELESS:
        return False
    # 「个」 in 「一个」 is `ge` where the character alone is `ge4`: the same
    # syllable, unstressed. Nothing to substitute for.
    return usual.rstrip("012345") != want.rstrip("012345") or want[-1:].isdigit()


@functools.lru_cache(maxsize=4096)
def _ambiguous(char: str) -> bool:
    """Whether this character has a second reading to be got wrong."""
    try:
        from pypinyin import Style, pinyin
    except ImportError:  # pragma: no cover
        return False
    return len(pinyin(char, heteronym=True, style=Style.TONE3)[0]) > 1


def _words(text: str) -> list[tuple[int, str]]:
    """`(offset, word)` for each word, or the whole text as one when unsegmented."""
    try:
        import jieba
    except ImportError:  # pragma: no cover
        return [(0, text)]
    found: list[tuple[int, str]] = []
    at = 0
    for word in jieba.cut(text, HMM=False):
        found.append((at, word))
        at += len(word)
    return found
