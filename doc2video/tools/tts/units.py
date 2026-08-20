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

Units are several sentences, not one. A synthesiser starts each utterance
fresh; one sentence at a time gives a narrator who takes a breath after every
full stop, which trades one kind of machine for another.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Long enough that the voice settles into a phrase, short enough that it has
# not flattened out. Measured in the text's own estimated seconds, because
# nothing has been spoken yet when the grouping happens.
TARGET_UNIT_SECONDS = 9.0
MAX_UNIT_SECONDS = 15.0

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
PAUSE_SENTENCE = 0.10
# Before the sentence the writer marked as the point of the page. Roughly a
# second all told, once the engine's own trailing silence is counted: that is
# what "listen to this one" sounds like.
PAUSE_EMPHASIS = 0.55
# Between one idea and the next, where the script turns.
PAUSE_TURN = 0.30

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

    units: list[Unit] = []
    current = Unit()
    spent = 0.0
    for index, sentence in enumerate(sentences):
        length = max(measure(sentence), 0.05)
        starts_here = _breaks_before(sentence, flags[index])
        # Break when the unit is long enough, or when this sentence wants to
        # start one — an emphasised line spoken mid-breath is not emphasised.
        if current.texts and (
            spent + length > MAX_UNIT_SECONDS
            or (spent >= TARGET_UNIT_SECONDS)
            or (starts_here and spent >= TARGET_UNIT_SECONDS / 2)
        ):
            units.append(current)
            current = Unit(pause_before=starts_here or PAUSE_SENTENCE)
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
        return PAUSE_EMPHASIS
    head = sentence.lstrip("　 \t")[:4]
    if any(head.startswith(turn) for turn in TURNS):
        return PAUSE_TURN
    return 0.0
