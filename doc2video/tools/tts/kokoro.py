"""Kokoro: a small neural voice, for when the built-in one sounds like a table.

The problem it solves is measurable rather than a matter of taste. `say`
pauses the same length at every mark — on one real page its pauses varied by a
coefficient of 0.13, which is another way of saying it looks up a number in a
punctuation table and uses it. Kokoro's varied by 0.66 on the same sentences,
and it ran 4.2 seconds between breaths where `say` never went past 2.0. That
difference is what people hear as reading versus speaking.

    停顿离散度   say 0.13   kokoro 0.66
    最长连讲     say 2.0s   kokoro 4.2s
    速度         say ~13×   kokoro 9.55× realtime (warm)

Not a dependency of this project, and deliberately not in the runtime: it
brings torch, and the packaged app's first launch is already the slowest thing
about installing it. It is picked up when it happens to be installed, which is
what makes it usable today and what a downloadable voice pack would install
tomorrow. Absent, everything falls back exactly as before.
"""

from __future__ import annotations

import wave
from pathlib import Path

from ...core.errors import ToolFailed
from ...core.logging import get_logger
from .base import TTSProvider, audio_duration

log = get_logger(__name__)

# Kokoro's own sample rate. Resampling here would cost quality for nothing —
# the pipeline mixes clips through ffmpeg, which handles rates itself.
SAMPLE_RATE = 24000
# Picked by ear against the other seven, on a page of real narration: the
# steadiest of them for explaining something. The others are still selectable.
DEFAULT_VOICE = "zm_yunxi"
# `z` is Mandarin. The voices are named by language, gender and speaker.
LANG_CODE = "z"
VOICES = (
    "zf_xiaobei",
    "zf_xiaoni",
    "zf_xiaoxiao",
    "zf_xiaoyi",
    "zm_yunjian",
    "zm_yunxi",
    "zm_yunxia",
    "zm_yunyang",
)

_pipeline = None


class KokoroProvider(TTSProvider):
    name = "kokoro"
    # 316 characters a minute at its own default, which is faster than anyone
    # wants a technical deck read to them. 0.85 puts it near 288, in the band
    # a listener follows.
    natural_rate = 0.85

    def available(self) -> bool:
        return self._import_error() is None

    def unavailable_reason(self) -> str:
        return f"未安装 kokoro（{self._import_error()}）"

    @staticmethod
    def _import_error() -> str | None:
        try:
            import kokoro  # noqa: F401
        except Exception as exc:  # ImportError, or a broken native extension
            return str(exc)[:120]
        return None

    def voices(self) -> list[str]:
        return list(VOICES) if self.available() else []

    def synthesize(self, text: str, out_path: Path, *, voice: str = "", rate: float = 1.0) -> float:
        """Write `text` as speech. `rate` stretches time, as everywhere else here."""
        global _pipeline

        try:
            import numpy as np
            from kokoro import KPipeline
        except Exception as exc:  # noqa: BLE001
            raise ToolFailed("Kokoro 不可用", detail={"error": str(exc)[:200]}) from exc

        # Loading costs about two seconds and is worth doing once: a deck is
        # thirty of these calls, and paying it thirty times is a minute of
        # nothing happening.
        if _pipeline is None:
            _pipeline = KPipeline(lang_code=LANG_CODE)

        chosen = voice if voice in VOICES else DEFAULT_VOICE
        try:
            chunks = list(_pipeline(text, voice=chosen, speed=max(rate, 0.5)))
            if not chunks:
                raise ToolFailed("Kokoro 没有产出音频", detail={"voice": chosen})
            audio = np.concatenate([chunk.audio.numpy() for chunk in chunks])
        except ToolFailed:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ToolFailed("Kokoro 合成失败", detail={"error": str(exc)[:200]}) from exc

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out_path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(SAMPLE_RATE)
            handle.writeframes((np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes())
        return audio_duration(out_path) or len(audio) / SAMPLE_RATE
