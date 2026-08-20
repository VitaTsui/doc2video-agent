"""TTS tool: text in, audio file plus sentence timestamps out."""

from __future__ import annotations

import contextlib
from pathlib import Path

from ...core import ledger
from ...core.config import Settings, get_settings
from ...core.logging import get_logger
from . import align
from .base import (
    Segment,
    TTSProvider,
    TTSResult,
    allocate_segments,
    estimate_duration,
    join_units,
    weight_of,
)
from .providers import resolve_provider
from .units import plan_units

log = get_logger(__name__)


# Which of the built-in voices is which. macOS reports no gender, and asking
# for 「女声」 is how people ask — so the mapping is stated once, here, for the
# voices that ship with the system in Chinese.
VOICE_GENDER = {
    "Tingting": "female",
    "Sinji": "female",
    "Meijia": "female",
    "Flo": "female",
    "Sandy": "female",
    "Shelley": "female",
    "Grandma": "female",
    # Piper, by model name: the one the runtime ships on Windows and Linux.
    "zh_CN-huayan-medium": "female",
    "zh_CN-huayan-x_low": "female",
    "Eddy": "male",
    "Reed": "male",
    "Rocko": "male",
    "Grandpa": "male",
}


def voices_available(settings: Settings | None = None) -> list[str]:
    """The Chinese voices this machine can actually speak with.

    Asked of whichever provider is in use, because the answer differs in kind:
    a dozen built into macOS, one model file shipped with the runtime on
    Windows and Linux, none at all when the provider is silence. Empty is the
    honest answer for the last of those — a menu that changes nothing is worse
    than no menu.
    """
    settings = settings or get_settings()
    return resolve_provider(settings.tts_provider).voices()


class TTSTool:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._provider: TTSProvider = resolve_provider(self._settings.tts_provider)
        log.info("TTS provider: %s", self._provider.name)

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def voice(self) -> str:
        return self._settings.tts_voice

    def synthesize(
        self,
        text: str,
        out_path: Path,
        *,
        sentences: list[str] | None = None,
        emphasis: list[bool] | None = None,
        voice: str = "",
        rate: float = 0.0,
    ) -> TTSResult:
        """Speak ``text``, and say when each of its sentences happens.

        `voice` and `rate` override what the machine is configured with — a
        project chooses its own, and the machine's values are the default it
        starts from. `emphasis` is the writer's mark on the sentences that
        matter, and it decides where the beats go.
        """
        lines = sentences or [text]
        segments, duration, source = self._speak(
            lines,
            out_path,
            emphasis=emphasis,
            voice=voice or self._settings.tts_voice,
            rate=rate or self._settings.tts_speech_rate,
        )
        return TTSResult(
            path=out_path,
            duration=duration,
            provider=self._provider.name,
            voice=voice or self._settings.tts_voice,
            segments=segments,
            timing_source=source,
        )

    def _speak(
        self,
        sentences: list[str],
        out_path: Path,
        *,
        emphasis: list[bool] | None,
        voice: str,
        rate: float,
    ) -> tuple[list[Segment], float, str]:
        """Write the clip, in units, and report exactly when each unit lands.

        A page spoken in one go comes back in one voice — the engine settles on
        an average pace and holds it, pausing the same length at every mark.
        Spoken in units the beats between them are ours: longer before the
        sentence the writer marked, longer where the script turns.

        The timing follows for free. Each unit is measured as it is written, so
        every unit boundary is exact; only the sentences *inside* a unit still
        need the ladder.
        """
        units = plan_units(sentences, emphasis=emphasis)
        if len(units) <= 1:
            text = "".join(sentences)
            with ledger.call(f"tts:{self._provider.name}", f"{len(text)} 字"):
                duration = self._provider.synthesize(text, out_path, voice=voice, rate=rate)
            segments, source = self._time(text, out_path, sentences, duration)
            return segments, duration, source

        work = out_path.parent / f".{out_path.stem}.units"
        work.mkdir(parents=True, exist_ok=True)
        clips: list[Path] = []
        try:
            for index, unit in enumerate(units):
                clip = work / f"{index:02d}.wav"
                with ledger.call(f"tts:{self._provider.name}", f"{len(unit.text)} 字"):
                    self._provider.synthesize(unit.text, clip, voice=voice, rate=rate)
                clips.append(clip)

            windows = join_units(clips, [unit.pause_before for unit in units], out_path)
            segments: list[Segment] = []
            for unit, clip, (start, end) in zip(units, clips, windows, strict=True):
                inner, _ = self._time(unit.text, clip, unit.texts, end - start)
                segments.extend(
                    Segment(
                        text=part.text,
                        start=round(start + part.start, 3),
                        end=round(start + part.end, 3),
                    )
                    for part in inner
                )
            duration = windows[-1][1] if windows else 0.0
            return segments, duration, "units"
        finally:
            for clip in clips:
                clip.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                work.rmdir()

    def _time(
        self, text: str, audio: Path, sentences: list[str], duration: float
    ) -> tuple[list[Segment], str]:
        """When each sentence starts, by the best means available.

        Three rungs, best first. The camera points at the moment a sentence
        begins, so the difference between them is the difference between the
        box appearing on the right phrase and appearing a second after it:

        1. the engine's own timings, when it reports any;
        2. the pauses measured in the clip that was just written;
        3. the clip's duration split in proportion to sentence length.

        The third is where this project started and it stays as the floor —
        a clip with no detectable pauses still has to be cut up somehow — but
        it is an estimate, and a run that falls back to it records that it did.
        """
        if reported := self._provider.timings(text, audio, duration):
            return reported, "provider"

        weights = [weight_of(line) for line in sentences]
        if measured := align.boundaries(audio, sentences, duration, weights):
            starts = [0.0, *measured]
            ends = [*measured, duration]
            return (
                [
                    Segment(text=line, start=round(start, 3), end=round(end, 3))
                    for line, start, end in zip(sentences, starts, ends, strict=True)
                ],
                "silence",
            )

        # Not a degradation worth a record per scene — it is the normal path on
        # a clip with no pauses (one short sentence), and thirty of them would
        # bury the records that matter. The source travels with the result.
        return allocate_segments(sentences, duration), "estimate"


__all__ = [
    "Segment",
    "TTSProvider",
    "TTSResult",
    "TTSTool",
    "allocate_segments",
    "estimate_duration",
]
