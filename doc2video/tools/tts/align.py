"""Where each sentence actually falls inside a clip.

The director points the camera at the moment a sentence starts. Until now that
moment was *estimated*: the clip's duration split across its sentences in
proportion to their length. Measured against the pauses in the audio, over the
62 sentence boundaries of one real 30-page deck, that estimate sat a median of
0.20s away, 0.63s at the 95th percentile and 0.86s at worst — far enough for a
box to appear around something the narrator has already finished with.

Nothing here synthesises anything. It reads a clip that was already made and
finds the pauses in it, which is what a sentence boundary sounds like. The
tool is ffmpeg's `silencedetect`, chosen over a speech model for a reason that
also applies elsewhere in this project: ffmpeg is already in the runtime, and a
model would be another few hundred megabytes on a first-run download that is
already the slowest thing about installing this app.

The result is one rung of a ladder, not the answer:

    provider timestamps  >  silence alignment  >  proportional estimate

A provider that reports real timings should be believed; this catches the ones
that do not; the estimate stays as the floor, because a clip with no detectable
pauses still has to be cut into sentences somehow.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ...core.logging import get_logger
from .. import ffmpeg

log = get_logger(__name__)

# What counts as a pause. Speech dips below this between sentences and, less
# deeply, between clauses; the threshold is deliberately generous because the
# clips are synthesised and have a clean noise floor.
SILENCE_DB = -35.0
# Shorter than this is a breath inside a sentence, not a boundary between two.
MIN_SILENCE = 0.18
# How much a longer pause is worth when two candidates fit about equally well.
# Sentence gaps tend to be longer than the ones inside a sentence, so a pause
# that is twice as long is preferred over one a fifth of a second closer to
# where the text guessed.
PAUSE_BONUS = 1.0

_SILENCE_END = re.compile(r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)")


@dataclass
class Pause:
    """One gap in the speech: when it ended, and how long it was."""

    end: float
    duration: float

    @property
    def start(self) -> float:
        return max(self.end - self.duration, 0.0)

    @property
    def middle(self) -> float:
        """A boundary sits in the gap, not at either edge of it."""
        return (self.start + self.end) / 2


def find_pauses(audio: Path) -> list[Pause]:
    """Every silence in the clip, in order. Empty when ffmpeg cannot say."""
    cmd = [
        ffmpeg.binary_path(),
        "-hide_banner",
        "-i",
        str(audio),
        "-af",
        f"silencedetect=noise={SILENCE_DB}dB:d={MIN_SILENCE}",
        "-f",
        "null",
        "-",
    ]
    try:
        # silencedetect writes to stderr, and ffmpeg exits 0 either way.
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    except (subprocess.SubprocessError, OSError) as exc:
        log.debug("静音检测失败：%s", exc)
        return []
    return [
        Pause(end=float(end), duration=float(duration))
        for end, duration in _SILENCE_END.findall(result.stderr)
    ]


def boundaries(
    audio: Path, sentences: list[str], duration: float, weights: list[float]
) -> list[float] | None:
    """The measured start of every sentence after the first, or None.

    `weights` is where the text says each boundary should be — the same
    proportional split the estimate uses, used here only to keep the pauses in
    the right order, not as a fence around how far a boundary may move.

    None means this clip yielded nothing worth using and the caller should stay
    with the estimate: a partly measured set is harder to reason about than a
    consistently estimated one, and worse to debug when a cue lands wrong.
    """
    if len(sentences) < 2 or duration <= 0:
        return None
    pauses = [p for p in find_pauses(audio) if 0.0 < p.middle < duration]
    expected = _expected_boundaries(duration, weights)
    if len(pauses) < len(expected):
        # Fewer gaps than sentences: something is wrong with one or the other,
        # and half-measured boundaries are harder to reason about than none.
        return None

    chosen = _assign(pauses, expected)
    if chosen is None:
        return None
    log.debug("静音对齐：%d 个句子边界是量出来的", len(chosen))
    return chosen


def _assign(pauses: list[Pause], expected: list[float]) -> list[float] | None:
    """Pick one pause per boundary, in order, closest to where the text says.

    Order-preserving on purpose. Choosing each boundary independently lets two
    of them land on the same gap, or the second land before the first, and a
    sentence that ends before it starts breaks everything downstream.

    Deliberately not fenced by a distance limit. The first version rejected any
    pause too far from its estimate, which threw away exactly the corrections
    worth having: the further the estimate is off, the further the real pause
    is from it. What keeps a clause gap from being taken for a sentence gap is
    that some other boundary fits it better, and that is a question about the
    whole assignment rather than about one boundary at a time.
    """
    count, total = len(expected), len(pauses)
    if count == 0 or total < count:
        return None

    def cost(index: int, at: int) -> float:
        return abs(pauses[at].middle - expected[index]) - PAUSE_BONUS * pauses[at].duration

    # best[i][j]: least cost of placing boundaries i.. using pauses j..
    best = [[float("inf")] * (total + 1) for _ in range(count + 1)]
    take = [[False] * (total + 1) for _ in range(count + 1)]
    for j in range(total + 1):
        best[count][j] = 0.0
    for i in range(count - 1, -1, -1):
        for j in range(total - 1, -1, -1):
            skip = best[i][j + 1]
            use = cost(i, j) + best[i + 1][j + 1]
            best[i][j] = min(skip, use)
            take[i][j] = use <= skip

    picked: list[float] = []
    i = j = 0
    while i < count and j < total:
        if take[i][j]:
            picked.append(pauses[j].middle)
            i += 1
        j += 1
    return picked if len(picked) == count else None


def _expected_boundaries(duration: float, weights: list[float]) -> list[float]:
    """Where the proportional split puts each boundary — the starting guess."""
    total = sum(weights)
    if total <= 0:
        return []
    cursor = 0.0
    out: list[float] = []
    for weight in weights[:-1]:
        cursor += duration * weight / total
        out.append(cursor)
    return out
