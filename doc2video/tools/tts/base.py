"""TTS interface and shared timing helpers.

Providers only have to turn text into an audio file. Sentence-level timestamps
are derived here, uniformly, so swapping providers never changes how the
director binds narration to on-screen elements.
"""

from __future__ import annotations

import contextlib
import re
import subprocess
import sys
import wave
from array import array
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

    # How fast this engine actually speaks Chinese, in characters a second.
    #
    # The writing budget is computed from this before a word is written, so a
    # wrong number here guarantees a video that misses its target length no
    # matter how well the script is written. Measured on the same page of real
    # narration: `say` 4.75, Edge's Yunyang 4.15 — the single shared constant
    # that used to serve both put Edge 11% over before the model had done
    # anything wrong.
    chars_per_second = 4.6

    # The multiplier that gives this engine a comfortable narration pace.
    #
    # `1.0` does not mean the same thing to two engines: `say` lands near 266
    # characters a minute at its own default, Kokoro near 316 — fast enough to
    # be the complaint people actually make. So the engine declares what its
    # own comfortable speed is, and a request like 「慢一点」 is applied on top
    # of that rather than instead of it.
    natural_rate = 1.0

    # The voice this engine uses when nobody names one. Declared rather than
    # left implicit so the window can answer 「现在用的是哪个声音」 without
    # synthesising something to find out.
    default_voice = ""

    def available(self) -> bool:
        return False

    #: Whether this engine has anywhere to put 「a word starts here」. When it
    #: does not, measuring a clip for cut words buys nothing — there would be
    #: no way to act on the answer.
    honours_phrase_boundary = False

    def phrase_boundary(self, text: str) -> str:
        """Turn a space in spoken text into whatever this engine reads as 「别在这里断」.

        A synthesiser decides its own phrase boundaries, and it gets them wrong
        on terms it does not know. Measured on 「国家人工智能应用中试基地」:
        every engine tried breaks after 中, reading 「应用中」 as a phrase and
        leaving 「试基地」 stranded — macOS `say` stops 0.27 seconds there, which
        is long enough to hear as a word being cut in half.

        A space is how a person writes 「这两个字属于下一个词」, and it is what
        the pronunciation dictionary can carry (「应用中试」 念 「应用 中试」).
        Engines differ in what they do with it: `say` ignores it outright. So
        each one renders it in its own terms, and the default is to leave it be.
        """
        return text

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


def estimate_duration(text: str, rate: float = 1.0, chars_per_second: float | None = None) -> float:
    """Estimate spoken duration from text, mixing CJK and Latin scripts.

    `chars_per_second` is the engine that will actually speak it. Left out, the
    figure is the middle of the ones this project has measured — fine for a
    rough guess and wrong by a tenth for whichever engine is furthest from it.
    """
    pace = chars_per_second or CJK_CHARS_PER_SECOND
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    latin_words = len([w for w in _latin_only(text).split() if w])
    seconds = cjk / pace + latin_words / LATIN_WORDS_PER_SECOND
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

    The engine's own silence is trimmed off first, so these two numbers mean
    what they say. Without that they were a floor rather than a value: Edge
    ends a clip with the better part of a second of nothing, and a page turn
    measured 2.39 seconds against a designed 1.5 — thirty of those is seventy
    seconds of dead air in a ten-minute film.

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

    frames = _trim_quiet_ends(frames, params)

    with wave.open(str(path), "wb") as handle:
        handle.setparams(params)
        handle.writeframes(_silence(lead) + frames + _silence(tail))
    return audio_duration(path)


# Anything under this counts as nothing being said. Low enough to survive the
# noise floor of a neural voice, high enough to catch the tail it leaves.
QUIET = 0.02
# Never take more than this off either end: a clip that measures quiet all the
# way through is a clip this should leave alone rather than erase.
MAX_TRIM_SECONDS = 1.5


def _trim_quiet_ends(frames: bytes, params) -> bytes:
    """Drop the engine's own silence from both ends, keeping the speech.

    Only 16-bit PCM, which is what every provider here writes; anything else
    is returned untouched rather than guessed at.
    """
    if params.sampwidth != 2 or not frames:
        return frames

    audio = array("h")
    audio.frombytes(frames[: len(frames) - len(frames) % 2])
    if sys.byteorder == "big":  # WAV is little-endian
        audio.byteswap()

    step = params.nchannels
    limit = int(QUIET * 32767)
    window = max(1, int(params.framerate * 0.01)) * step  # 10ms
    most = int(MAX_TRIM_SECONDS * params.framerate) * step

    def loud(at: int) -> bool:
        chunk = audio[at : at + window]
        return bool(chunk) and max(max(chunk), -min(chunk)) > limit

    # A clip with nothing loud anywhere is the silent provider's, and it is
    # the whole scene: trimming it would leave a page with no duration at all.
    if not any(loud(at) for at in range(0, len(audio), window)):
        return frames

    head = 0
    while head < min(most, len(audio)) and not loud(head):
        head += window
    tail = len(audio)
    while tail > max(len(audio) - most, head) and not loud(max(head, tail - window)):
        tail -= window

    if tail <= head:
        return frames
    start = head * params.sampwidth
    end = tail * params.sampwidth
    return frames[start:end]


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


def silences(path: Path, *, floor: float = 0.06) -> list[tuple[float, float]]:
    """Every quiet stretch in a clip, as `(start, length)` in seconds.

    Used to hear what the engine did rather than assume it: a gap the script
    did not ask for is either a mark being read or a word being cut in half,
    and only the clip can say which one happened where.
    """
    from ...core import programs

    ffmpeg = programs.find("ffmpeg")
    if ffmpeg is None:
        return []
    try:
        proc = subprocess.run(  # noqa: S603 - argv built here
            [ffmpeg, "-i", str(path), "-af", f"silencedetect=noise=-40dB:d={floor}",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    pattern = re.compile(r"silence_start: ([\d.]+)[\s\S]*?silence_duration: ([\d.]+)")
    return [(float(at), float(length)) for at, length in pattern.findall(proc.stderr)]


def join_units(clips: list[Path], pauses: list[float], out_path: Path) -> list[tuple[float, float]]:
    """Write `clips` end to end with `pauses[i]` of silence before clip i.

    Returns each clip's window in the joined file. The windows are exact — the
    silence is written here, frame by frame, so where one unit stops and the
    next begins is arithmetic on sample counts rather than something to be
    measured or guessed at afterwards.

    Standard library only, like `pad_silence`: joining speech should not cost
    an external binary, and re-encoding to join would lose the sample-exact
    boundaries that are the point.
    """
    if not clips:
        return []

    windows: list[tuple[float, float]] = []
    frames = bytearray()
    params = None
    for clip, pause in zip(clips, pauses, strict=True):
        with wave.open(str(clip), "rb") as handle:
            if params is None:
                params = handle.getparams()
            body = handle.readframes(handle.getnframes())
        # The engine's own silence comes off first, so the gap between two
        # units is the number asked for and nothing else. Left on, a 「句号后
        # 停 0.10 秒」 measured 1.44 seconds on a real film — the pause we
        # chose plus two clips' worth of edges — while the sentences inside a
        # unit ran 0.79. The beats were the wrong way round.
        body = _trim_quiet_ends(body, params)
        gap = max(0, int(max(pause, 0.0) * params.framerate))
        frames += b"\x00" * (gap * params.sampwidth * params.nchannels)
        start = len(frames) / (params.framerate * params.sampwidth * params.nchannels)
        frames += body
        end = len(frames) / (params.framerate * params.sampwidth * params.nchannels)
        windows.append((round(start, 4), round(end, 4)))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as handle:
        handle.setparams(params)
        handle.writeframes(bytes(frames))
    return windows
