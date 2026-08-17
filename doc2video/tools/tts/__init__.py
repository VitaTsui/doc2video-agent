"""TTS tool: text in, audio file plus sentence timestamps out."""

from __future__ import annotations

from pathlib import Path

from ...core.config import Settings, get_settings
from ...core.logging import get_logger
from .base import Segment, TTSProvider, TTSResult, allocate_segments, estimate_duration
from .providers import resolve_provider

log = get_logger(__name__)


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
        self, text: str, out_path: Path, *, sentences: list[str] | None = None
    ) -> TTSResult:
        """Synthesize ``text`` and time-align its sentences within the clip."""
        duration = self._provider.synthesize(
            text,
            out_path,
            voice=self._settings.tts_voice,
            rate=self._settings.tts_speech_rate,
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
