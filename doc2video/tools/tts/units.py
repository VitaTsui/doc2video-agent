"""Speech units: how a page is broken up before it is spoken.

A page handed to a synthesiser in one piece comes back in one voice. The
engine picks an average pace somewhere in the first sentence and holds it for
forty seconds, and every pause it makes is the pause its punctuation table says
to make — the same length at every comma, the same length at every full stop.
That is most of what people hear as "machine reading".

So a page is spoken as several units, and the gaps between them are ours to
choose rather than the engine's. Two things follow:

* **The pause carries meaning.** A beat before a sentence the model marked as
  the important one is emphasis; the beat at the start of a new idea is a
  paragraph. Both come out of what the narration already records — nobody has
  to annotate anything by hand.
* **The timing stops being a guess.** Each unit's duration is measured when it
  is written, so the sentence boundaries inside a scene are known exactly
  rather than inferred from the pauses in a single long clip.

**Where a unit ends is a question about the language, not about the clock.**
The first version broke whenever nine seconds had gone by, which put the long
pause wherever the stopwatch happened to land. Measured on a thirty-page film:
the gaps we chose ran a median of 1.44 seconds and the ones we left to the
engine ran 0.79 — so which sentence got a beat, and which two ran together,
was decided by arithmetic on a duration estimate. A listener hears that as a
narrator pausing in odd places and hurrying past the ones that mattered.

So a sentence begins a new unit when the sentence itself says so: it turns
（不过、所以、再看）, it is the one the writer marked, it opens an item in a
list. Sentences that say nothing of the kind are spoken together, which is
also what keeps a synthesiser from taking a breath after every full stop.
Length is a backstop now, not the rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core import tuning

# A backstop, not the rule: a unit that runs this long has stopped being a
# phrase whatever the words are doing. Measured in the text's own estimated
# seconds, because nothing has been spoken yet when the grouping happens.
TARGET_UNIT_SECONDS = 9.0
MAX_UNIT_SECONDS = 15.0

# A sentence shorter than this is not left to stand alone: 「先说背景。」 spoken
# as its own utterance comes out clipped, with a breath on either side of four
# words. It joins the next one unless that one starts something.
SHORT_SENTENCE_SECONDS = 2.2

# What the silence between two units says. A person does not pause the same
# length everywhere; these are the differences a listener reads as meaning
# rather than as a gap.
#
# Sized against what the engine already does, which is the part that is easy
# to get wrong. Measured on a real page: `say` leaves 0.28–0.51s at its own
# punctuation, clustering near 0.4. A "semantic" beat of 0.42 added to that
# comes out indistinguishable from an ordinary comma — the intent was there
# and the listener could not hear it. These are *additional* silence, so the
# emphasis beat has to clear the engine's own pause to read as one.
# Each unit clip has its own edge silence trimmed before the units are joined,
# so these are the whole gap rather than something added to whatever the
# engine left behind. Re-based on what the engine does *inside* a unit, which
# is the rhythm a listener is already hearing: measured at 0.79s between two
# sentences it speaks in one breath. A plain full stop we control should sit
# just under that; a beat that means something has to clear it.
# Level with what the engine leaves between two sentences it speaks in one
# breath — measured at 0.77s. Shorter and the rhythm inverts: a boundary we
# chose on purpose would pass quicker than one nobody decided anything about.
PAUSE_SENTENCE = 0.75
# Before the sentence the writer marked as the point of the page.
PAUSE_EMPHASIS = 1.20
# Between one idea and the next, where the script turns.
PAUSE_TURN = 0.95

# The words a script turns on. Not an exhaustive list of connectives — only
# the ones that begin a new movement rather than continue the current one.
TURNS = (
    "但是", "不过", "反过来", "另一方面", "接下来", "回到", "所以", "因此",
    "第一", "第二", "第三", "首先", "其次", "最后", "总的来说", "换句话说",
)


@dataclass
class Unit:
    """A run of sentences spoken as one utterance, and the beat before it."""

    texts: list[str] = field(default_factory=list)
    pause_before: float = 0.0

    @property
    def text(self) -> str:
        return "".join(self.texts)


def plan_units(
    sentences: list[str], *, emphasis: list[bool] | None = None, weight=None
) -> list[Unit]:
    """Group `sentences` into utterances, and decide the beat before each.

    `emphasis` is the writer's own mark on the sentence that matters — the
    narration skill sets it, and it is the only semantic signal here that
    nobody had to invent.
    """
    from .base import estimate_duration

    measure = weight or estimate_duration
    flags = list(emphasis or [])
    flags += [False] * (len(sentences) - len(flags))

    # Read once per page rather than per sentence: these are the numbers as
    # they are set right now, and someone may have set them.
    target = tuning.value("voice.unit_seconds")
    longest = max(MAX_UNIT_SECONDS, target * MAX_UNIT_SECONDS / TARGET_UNIT_SECONDS)
    sentence_pause = tuning.value("voice.pause_sentence")

    units: list[Unit] = []
    current = Unit()
    spent = 0.0
    for index, sentence in enumerate(sentences):
        length = max(measure(sentence), 0.05)
        starts_here = _breaks_before(sentence, flags[index])
        # A sentence begins a new unit when the sentence says so. What it says
        # is: it turns, it is the marked one, or the one before it was too
        # short to stand alone — that last is the only length still in this
        # decision, and it is about the *previous* sentence, not the clock.
        breaks = bool(current.texts) and (
            starts_here > 0 or spent >= SHORT_SENTENCE_SECONDS or spent + length > longest
        )
        if breaks:
            units.append(current)
            current = Unit(pause_before=starts_here or sentence_pause)
            spent = 0.0
        current.texts.append(sentence)
        spent += length

    if current.texts:
        units.append(current)
    if units:
        # Nothing to pause before at the start; the scene's own lead silence
        # is already there.
        units[0].pause_before = 0.0
    return units


def _breaks_before(sentence: str, emphasised: bool) -> float:
    """The beat this sentence deserves in front of it, or 0 for none."""
    if emphasised:
        return tuning.value("voice.pause_emphasis")
    head = sentence.lstrip("　 \t")[:4]
    if any(head.startswith(turn) for turn in TURNS):
        return tuning.value("voice.pause_turn")
    return 0.0
