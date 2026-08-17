"""TTS interface and shared timing helpers.

Providers only have to turn text into an audio file. Sentence-level timestamps
are derived here, uniformly, so swapping providers never changes how the
director binds narration to on-screen elements.
"""

from __future__ import annotations

import contextlib
import wave
from dataclasses import dataclass, field
from pathlib import Path

# Rough speaking rates used to estimate duration before audio exists.
CJK_CHARS_PER_SECOND = 4.6
LATIN_WORDS_PER_SECOND = 2.6


@dataclass
class Segment:
    """One sentence of narration with its time window inside the clip."""

    text: str
    start: float = 0.0
    end: float = 0.0


@dataclass
class TTSResult:
    path: Path
    duration: float
    provider: str
    voice: str = ""
    segments: list[Segment] = field(default_factory=list)


class TTSProvider:
    name = "base"

    def available(self) -> bool:
        return False

    def synthesize(self, text: str, out_path: Path, *, voice: str = "", rate: float = 1.0) -> float:
        """Write audio for ``text`` to ``out_path`` and return its duration."""
        raise NotImplementedError


# --------------------------------------------------------------------------
# timing
# --------------------------------------------------------------------------


def estimate_duration(text: str, rate: float = 1.0) -> float:
    """Estimate spoken duration from text, mixing CJK and Latin scripts."""
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    latin_words = len([w for w in _latin_only(text).split() if w])
    seconds = cjk / CJK_CHARS_PER_SECOND + latin_words / LATIN_WORDS_PER_SECOND
    return max(0.8, seconds / max(rate, 0.1))


def _latin_only(text: str) -> str:
    return "".join(" " if "一" <= ch <= "鿿" else ch for ch in text)


def weight_of(text: str) -> float:
    """Relative speaking weight of a sentence — the basis for timestamp split."""
    return max(estimate_duration(text), 0.05)


def allocate_segments(sentences: list[str], total_duration: float) -> list[Segment]:
    """Distribute a clip's duration across its sentences proportionally.

    Providers that return real word-level timestamps should override this; the
    proportional split keeps zoom/highlight cues within a fraction of a second
    of the sentence they belong to, which is the MVP accuracy bar (方案 §19).
    """
    sentences = [s for s in sentences if s.strip()]
    if not sentences or total_duration <= 0:
        return []
    weights = [weight_of(s) for s in sentences]
    total_weight = sum(weights)
    segments: list[Segment] = []
    cursor = 0.0
    for sentence, weight in zip(sentences, weights, strict=True):
        span = total_duration * weight / total_weight
        segments.append(Segment(text=sentence, start=round(cursor, 3), end=round(cursor + span, 3)))
        cursor += span
    segments[-1].end = round(total_duration, 3)
    return segments


# --------------------------------------------------------------------------
# duration probing
# --------------------------------------------------------------------------


def audio_duration(path: Path) -> float | None:
    """Read a clip's duration.

    The WAV header is exact and needs no external binary, so try it first; other
    containers fall through to ffprobe/ffmpeg.
    """
    if path.suffix.lower() == ".wav":
        with contextlib.suppress(wave.Error, OSError, EOFError):
            with wave.open(str(path), "rb") as handle:
                frames, framerate = handle.getnframes(), handle.getframerate()
                if framerate:
                    return frames / float(framerate)

    from ..media_binaries import probe_duration

    return probe_duration(path)
