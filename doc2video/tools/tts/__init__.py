"""TTS tool: text in, audio file plus sentence timestamps out."""

from __future__ import annotations

from pathlib import Path

from ...core import ledger
from ...core.config import Settings, get_settings
from ...core.logging import get_logger
from .base import Segment, TTSProvider, TTSResult, allocate_segments, estimate_duration
from .providers import resolve_provider

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
        voice: str = "",
        rate: float = 0.0,
    ) -> TTSResult:
        """Synthesize ``text`` and time-align its sentences within the clip.

        `voice` and `rate` override what the machine is configured with — a
        project chooses its own, and the machine's values are the default it
        starts from.
        """
        with ledger.call(f"tts:{self._provider.name}", f"{len(text)} 字"):
            duration = self._provider.synthesize(
                text,
                out_path,
                voice=voice or self._settings.tts_voice,
                rate=rate or self._settings.tts_speech_rate,
            )
        segments = allocate_segments(sentences or [text], duration)
        return TTSResult(
            path=out_path,
            duration=duration,
            provider=self._provider.name,
            voice=self._settings.tts_voice,
            segments=segments,
        )


__all__ = [
    "Segment",
    "TTSProvider",
    "TTSResult",
    "TTSTool",
    "allocate_segments",
    "estimate_duration",
]
