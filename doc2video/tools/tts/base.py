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

    def pause_markup(self, seconds: float) -> str:  # noqa: ARG002
        """How this engine is asked to hold for `seconds` in the middle of a call.

        Empty for an engine with nothing to say it with — that engine's clauses
        are spoken separately and the silence is written between the clips.
        """
        return ""

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

    Read straight out of the PCM when it can be — every provider here writes
    16-bit WAV, and a film is hundreds of clips: spawning ffmpeg for each one
    put two minutes on the test suite alone, for a measurement the samples
    already carry.
    """
    if path.suffix.lower() == ".wav":
        with contextlib.suppress(wave.Error, OSError, EOFError):
            return _quiet_runs(path, floor)

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


def _quiet_runs(path: Path, floor: float) -> list[tuple[float, float]]:
    """`silences` for 16-bit PCM, in samples rather than in a subprocess."""
    with wave.open(str(path), "rb") as handle:
        params = handle.getparams()
        frames = handle.readframes(params.nframes)
    if params.sampwidth != 2 or not frames:
        return []

    audio = array("h")
    audio.frombytes(frames[: len(frames) - len(frames) % 2])
    if sys.byteorder == "big":
        audio.byteswap()

    step = params.nchannels
    limit = int(QUIET * 32767)
    window = max(1, int(params.framerate * 0.01)) * step  # 10ms, as trimming uses
    seconds = window / step / params.framerate

    runs: list[tuple[float, float]] = []
    start: int | None = None
    for index, at in enumerate(range(0, len(audio), window)):
        chunk = audio[at : at + window]
        loud = bool(chunk) and max(max(chunk), -min(chunk)) > limit
        if loud and start is not None:
            length = (index - start) * seconds
            if length >= floor:
                runs.append((start * seconds, length))
            start = None
        elif not loud and start is None:
            start = index
    if start is not None:
        length = (len(audio) / window - start) * seconds
        if length >= floor:
            runs.append((start * seconds, length))
    return runs


#: What a gap nobody asked for is worth. Not zero — the engine put a phrase
#: boundary there and the words either side were spoken around it — but short
#: enough that it reads as phrasing rather than as the end of a sentence.
INVENTED_GAP = 0.10

#: How far a designed pause may be from the silence that claims it, in seconds
#: of estimated speech. Wide, because the estimate is proportional and a long
#: sentence drifts; a wrong claim only ever makes a gap the length of a
#: neighbouring designed pause.
CLAIM_TOLERANCE = 0.9

#: Below this a silence is part of speaking rather than a pause in it.
GAP_FLOOR = 0.13


def retime_gaps(path: Path, marks: list[tuple[int, float]], spoken_chars: int) -> float:
    """Cut every pause inside one clip back to the length it was designed to be.

    An engine asked to hold for 120 milliseconds does not hold for 120
    milliseconds: `say` re-plans its phrasing around the request and adds about
    a tenth of a second of its own, so a breath designed at 0.12 is heard at
    0.24 and reads as the end of a sentence rather than as a breath — reported
    as 「停顿太长，变得像是两句了」. Below roughly 200ms the request stops mattering
    at all: every value from 20 to 160 came back the same 0.26.

    Asking differently cannot fix that, so this stops asking and edits the
    result. Each silence is matched to the designed pause nearest it — position
    estimated from how much speech precedes it — and cut to that length.

    Silences no designed pause claims are the engine's own, including the ones
    that land inside a word. They are cut to `INVENTED_GAP` rather than removed:
    the words either side were spoken around a boundary, and closing it
    completely runs them together.

    Only ever shortens. A gap that already came back shorter than it was asked
    for is left alone, because lengthening it would insert silence into speech
    that was never planned around it.

    @param marks: `(characters spoken before it, seconds)` for each designed pause.
    @param spoken_chars: how many characters the clip says in total.
    @returns the clip's new duration.
    """
    with wave.open(str(path), "rb") as handle:
        params = handle.getparams()
        data = array("h")
        data.frombytes(handle.readframes(handle.getnframes()))

    rate = params.framerate
    total = len(data) / rate if rate else 0.0
    if total <= 0:
        return total
    inner = [
        (start, length)
        for start, length in silences(path, floor=GAP_FLOOR)
        if start > 0.05 and start + length < total - 0.05
    ]
    if not inner:
        return total

    speech = total - sum(length for _, length in inner)
    targets: list[float] = []
    for start, length in inner:
        # Where this silence falls in the *speaking*, which is the only thing
        # the character count can be compared against.
        spoken_before = start - sum(gap for at_, gap in inner if at_ < start)
        at = spoken_before / max(speech, 0.01) * spoken_chars
        claimed: float | None = None
        nearest = CLAIM_TOLERANCE
        for chars, seconds in marks:
            distance = abs(chars - at) / max(spoken_chars, 1) * total
            if distance < nearest:
                nearest, claimed = distance, seconds
        targets.append(min(length, claimed if claimed is not None else INVENTED_GAP))

    out = array("h")
    cursor = 0
    for (start, length), target in zip(inner, targets, strict=True):
        out.extend(data[cursor : int(start * rate)])
        out.extend([0] * int(target * rate))
        cursor = int((start + length) * rate)
    out.extend(data[cursor:])

    with wave.open(str(path), "wb") as handle:
        handle.setparams(params)
        handle.writeframes(out.tobytes())
    return len(out) / rate


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
