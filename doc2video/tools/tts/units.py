"""Speech units: how a page is broken up before it is spoken.

A page handed to a synthesiser in one piece comes back in one voice. The
engine picks an average pace somewhere in the first sentence and holds it for
forty seconds, and every pause it makes is the pause its punctuation table says
to make — the same length at every comma, the same length at every full stop.
That is most of what people hear as "machine reading".

So a page is spoken as several units, and the gaps between them are ours to
choose rather than the engine's. Each unit's duration is also measured as it
is written, so the sentence boundaries inside a scene are known exactly rather
than inferred from the pauses in one long clip.

**The punctuation decides.** Two earlier rules did not. The first broke a unit
whenever nine seconds had gone by, which put the long pause wherever the
stopwatch happened to land — mid-sentence, mid-name. The second read the
sentence for turns and emphasis, which was better and still left the ordinary
sentences to a leftover length test (「上一句不足 2.2 秒就并进来」), so whether
two sentences ran together depended on an estimate of how long they took to
say. Both produced the same complaint: a pause inside a complete sentence, and
no pause where the writing plainly asked for one.

A written line already says where it breathes. 。 ends a thought; ； separates
two that belong together; ： leans forward into what follows; ，is a beat the
engine already takes inside the unit. Reading the mark is not an approximation
of the intent — it *is* the intent, written down by whoever wrote the line.

Turns and emphasis still lengthen a gap, because 「不过」 after a full stop is a
longer beat than the same full stop elsewhere. They never create one: a beat
only ever falls where the punctuation already put it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ...core import tuning

# How long to wait after each mark. Sized against what the engine does inside
# a unit, which is the rhythm the listener is already hearing: measured at
# 0.77s between two sentences `say` speaks in one breath. A full stop we chose
# sits level with that; the marks that hold a thought open are shorter, and
# the ones that end it harder are longer.
#
# Lengthened after listening: the film at 0.28 and 0.75 was heard as hurried
# even at a speaking rate barely above the engine's own. The gaps are half of
# what 「快」 means, and they are the half we control exactly.
#
# 、 and ，are here too, because 「the engine takes them at its own pace」 turned
# out to mean 「as long as a full stop」. Measured on a finished page: one
# sentence of 143 characters came back with commas of 0.33, 0.42, 0.43 and 0.33
# seconds against the 0.75 we had chosen for the sentence boundary — so the
# rhythm inside a sentence was nearly the rhythm between two. Cutting at every
# mark costs more clips and buys the only thing that makes this predictable:
# every silence in the film is one somebody chose.
PAUSE_COMMA = 0.32      # ，— a breath, not a stop
PAUSE_ENUM = 0.16       # 、— the shortest thing that is still a gap
PAUSE_SENTENCE = 0.82   # 。 and a line that ends with no mark at all
PAUSE_EXCLAIM = 0.85    # ！？ — the sentence lands, then a beat
PAUSE_SEMICOLON = 0.55  # ；— two halves of one thought
PAUSE_COLON = 0.35      # ：— leans into what comes next
PAUSE_ELLIPSIS = 0.90   # …… — trailing off is the point
PAUSE_DASH = 0.50       # —— — an aside

# Before the sentence the writer marked as the point of the page.
PAUSE_EMPHASIS = 1.20
# Between one idea and the next, where the script turns.
PAUSE_TURN = 0.95

# Written with the mark last, so 「。」 does not match a line ending in 「……。」
# before the ellipsis rule gets to it.
MARKS: tuple[tuple[str, str], ...] = (
    ("……", "ellipsis"),
    ("，", "comma"),
    (",", "comma"),
    ("、", "enum"),
    ("...", "ellipsis"),
    ("——", "dash"),
    ("！", "exclaim"),
    ("？", "exclaim"),
    ("!", "exclaim"),
    ("?", "exclaim"),
    ("；", "semicolon"),
    (";", "semicolon"),
    ("：", "colon"),
    (":", "colon"),
    ("。", "sentence"),
    (".", "sentence"),
)

# The knob each mark reads. One per kind rather than one per character: 「?」 and
# 「？」 are the same pause to a listener.
KNOBS = {
    "comma": "voice.pause_comma",
    "enum": "voice.pause_enum",
    "sentence": "voice.pause_sentence",
    "exclaim": "voice.pause_exclaim",
    "semicolon": "voice.pause_semicolon",
    "colon": "voice.pause_colon",
    "ellipsis": "voice.pause_ellipsis",
    "dash": "voice.pause_dash",
}

# Closing quotes and brackets sit *after* the mark that matters: 「他说：『走。』」
# ends on a full stop even though its last character is a quote.
TRAILING = "\"'』」》）)】”’"

# The characters a clause may end on, and therefore where one is cut.
_MARK_CHARS = "，,、；;：:。.！!？?…—"
_CLAUSE = re.compile(rf"[^{_MARK_CHARS}]*[{_MARK_CHARS}]+|[^{_MARK_CHARS}]+")

# The words a script turns on. Not an exhaustive list of connectives — only
# the ones that begin a new movement rather than continue the current one.
TURNS = (
    "但是", "不过", "反过来", "另一方面", "接下来", "回到", "所以", "因此",
    "第一", "第二", "第三", "首先", "其次", "最后", "总的来说", "换句话说",
)


@dataclass
class Unit:
    """One clause, spoken on its own, and the beat that precedes it."""

    texts: list[str] = field(default_factory=list)
    pause_before: float = 0.0
    #: Which of the caller's sentences this clause came out of. The captions and
    #: the camera are still cut by sentence; only the speaking is by clause.
    sentence: int = 0

    @property
    def text(self) -> str:
        return "".join(self.texts)


def mark_of(text: str) -> str:
    """Which kind of mark this piece ends on. Unmarked pieces read as a full stop."""
    stripped = text.rstrip().rstrip(TRAILING).rstrip()
    for mark, kind in MARKS:
        if stripped.endswith(mark):
            return kind
    return "sentence"


def pause_after(text: str) -> float:
    """The silence this piece's own punctuation asks for."""
    return tuning.value(KNOBS[mark_of(text)])


def clauses(sentence: str) -> list[str]:
    """Cut a sentence at its marks, keeping each mark with the piece it ends.

    A piece with nothing but a mark in it (「……」 on its own, a stray comma) is
    joined to the one before: it is punctuation, not something to say.
    """
    pieces: list[str] = []
    for piece in _CLAUSE.findall(sentence):
        if not piece.strip():
            continue
        if pieces and not any(ch not in _MARK_CHARS + TRAILING + " 　" for ch in piece):
            pieces[-1] += piece
        else:
            pieces.append(piece)
    return pieces or ([sentence] if sentence.strip() else [])


def plan_units(
    sentences: list[str], *, emphasis: list[bool] | None = None, weight=None  # noqa: ARG001
) -> list[Unit]:
    """Cut `sentences` into clauses, and decide the beat before each.

    Every silence in the finished audio comes from here. A clause is spoken on
    its own, so the engine has no mark left inside it to pause at on its own
    schedule, and the gap that follows is the one its mark asks for — 、 shorter
    than ，, ，shorter than 。, and a turn or an emphasis longer still.

    `weight` is accepted and unused: it measured how long a sentence took to
    say, back when that decided where the units broke.
    """
    flags = list(emphasis or [])
    flags += [False] * (len(sentences) - len(flags))

    units: list[Unit] = []
    previous = ""
    for index, sentence in enumerate(sentences):
        pieces = clauses(sentence.strip())
        for position, piece in enumerate(pieces):
            gap = pause_after(previous) if previous else 0.0
            # The sentence's own beat — a turn, or the writer's mark — belongs
            # to the sentence, not to every clause inside it.
            own = _emphasis_of(piece, flags[index]) if position == 0 else 0.0
            units.append(
                Unit(texts=[piece], pause_before=max(gap, own), sentence=index)
            )
            previous = piece

    if units:
        # Nothing to pause before at the start; the scene's own lead silence
        # is already there.
        units[0].pause_before = 0.0
    return units


def _emphasis_of(sentence: str, emphasised: bool) -> float:
    """The longer beat this sentence earns, or 0 to leave the mark's own."""
    if emphasised:
        return tuning.value("voice.pause_emphasis")
    head = sentence.lstrip("　 \t")[:4]
    if any(head.startswith(turn) for turn in TURNS):
        return tuning.value("voice.pause_turn")
    return 0.0
