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

from ...core.logging import get_logger

log = get_logger(__name__)

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
    # Where the segment times came from: "provider" if the engine reported
    # them, "silence" if they were measured off the clip, "estimate" if they
    # were inferred from how long the sentences are. Carried because the three
    # are not equally trustworthy and the director acts on all of them the
    # same way — so the difference has to be visible somewhere.
    timing_source: str = "estimate"


class TTSProvider:
    name = "base"

    def available(self) -> bool:
        return False

    def synthesize(self, text: str, out_path: Path, *, voice: str = "", rate: float = 1.0) -> float:
        """Write audio for ``text`` to ``out_path`` and return its duration."""
        raise NotImplementedError

    def timings(self, text: str, out_path: Path, duration: float) -> list[Segment] | None:
        """Real sentence timings, when the engine reported them.

        None means "I don't know", which is the honest answer for every engine
        this project ships with today — `say` and Piper both hand back audio
        and nothing else. A provider that does know should say so here, and be
        believed over anything measured or estimated afterwards.
        """
        return None

    def voices(self) -> list[str]:
        """The voices this provider can actually speak Chinese with, here.

        Answered by the provider because the answer is different in kind on
        each platform: macOS has a dozen built in, Piper has whatever model
        files are on disk — the runtime ships exactly one — and silence has
        none. A caller offering the user a choice must not assume the macOS
        shape, or the choice is empty everywhere else.
        """
        return []


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


def pad_silence(path: Path, *, lead: float = 0.0, tail: float = 0.0) -> float | None:
    """Wrap a clip in silence, returning its new duration.

    Both ends earn their keep. The tail is the beat between pages: without it
    one scene's last word runs straight into the next slide. The lead is the
    page arriving before anyone speaks over it — a scene starts on a fade, and
    a narrator who begins during the transition is talking about something the
    viewer cannot see yet.

    It happens to the *audio* rather than to the timeline because everything
    downstream takes its timing from the clip: the scene holds its frames for
    exactly as long as the audio runs, and the subtitle cues — which come from
    the speech — sit inside the silence rather than over it.

    WAV only, written with the standard library so a pause costs no external
    binary. Anything else is left untouched rather than transcoded.
    """
    if (lead <= 0 and tail <= 0) or path.suffix.lower() != ".wav":
        return audio_duration(path)

    try:
        with wave.open(str(path), "rb") as handle:
            params = handle.getparams()
            frames = handle.readframes(handle.getnframes())
    except (wave.Error, OSError, EOFError) as exc:
        log.debug("无法为 %s 追加静音：%s", path.name, exc)
        return audio_duration(path)

    def _silence(seconds: float) -> bytes:
        samples = max(0, int(seconds * params.framerate))
        return b"\x00" * (samples * params.sampwidth * params.nchannels)

    with wave.open(str(path), "wb") as handle:
        handle.setparams(params)
        handle.writeframes(_silence(lead) + frames + _silence(tail))
    return audio_duration(path)


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
