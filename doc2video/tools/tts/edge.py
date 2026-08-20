"""The broadcast voice, which lives on someone else's machine.

Picked by ear over everything local: `zh-CN-YunyangNeural` at eight percent
below its own pace. Microsoft tags it Professional / Reliable and it is the
only Mandarin voice in this set with that character — the others are Warm,
Lively, Cute, Passion. For explaining a deck, that difference is the whole
point.

Two things about it are unlike every other provider here, and both are stated
rather than hidden:

**It needs the network.** Everything else in this project runs on the machine
it is installed on, and a video that cannot be made on a train is a different
product. So this is never chosen automatically — `AUTO_ORDER` stays local, and
someone has to ask for this one. Asked for and unreachable, the run falls back
to a local voice and records that it did, rather than failing a thirty-page
render at page nine.

**Its style tags do not work here.** Azure documents this voice as supporting
`newscast` and `narration-professional`, which would be the real broadcast
switch. The free read-aloud endpoint does not honour them — it reads the
markup out loud, turning an 11.3-second clip into a 17.3-second one that opens
by saying "mstts express as style newscast". Pace is the only control that
survives, which is why the character here comes from a rate and not a style.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from ...core.errors import ToolFailed
from ...core.logging import get_logger
from .. import ffmpeg
from .base import TTSProvider, audio_duration

log = get_logger(__name__)

# Tagged Professional / Reliable, and the one this project was tuned against.
DEFAULT_VOICE = "zh-CN-YunyangNeural"
VOICES = (
    "zh-CN-YunyangNeural",
    "zh-CN-YunjianNeural",
    "zh-CN-YunxiNeural",
    "zh-CN-YunxiaNeural",
    "zh-CN-XiaoxiaoNeural",
    "zh-CN-XiaoyiNeural",
    "zh-CN-liaoning-XiaobeiNeural",
    "zh-CN-shaanxi-XiaoniNeural",
)


class EdgeProvider(TTSProvider):
    name = "edge"
    # Eight percent under its own pace, which is where it stopped sounding
    # hurried on a page of real narration.
    natural_rate = 0.92
    # Measured over a 30-page deck at that pace: 2483 characters in 598
    # seconds of speech.
    chars_per_second = 4.15

    def available(self) -> bool:
        return self._import_error() is None

    def unavailable_reason(self) -> str:
        return f"未安装 edge-tts（{self._import_error()}）"

    @staticmethod
    def _import_error() -> str | None:
        try:
            import edge_tts  # noqa: F401
        except Exception as exc:
            return str(exc)[:120]
        return None

    def voices(self) -> list[str]:
        return list(VOICES) if self.available() else []

    def synthesize(self, text: str, out_path: Path, *, voice: str = "", rate: float = 1.0) -> float:
        try:
            import edge_tts
        except Exception as exc:  # noqa: BLE001
            raise ToolFailed("edge-tts 不可用", detail={"error": str(exc)[:200]}) from exc

        chosen = voice if voice in VOICES else DEFAULT_VOICE
        # The endpoint speaks percentages, this project speaks multipliers.
        percent = round((max(rate, 0.5) - 1.0) * 100)

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
            raw = Path(handle.name)
        try:
            asyncio.run(
                edge_tts.Communicate(text, chosen, rate=f"{percent:+d}%").save(str(raw))
            )
            if not raw.exists() or raw.stat().st_size == 0:
                raise ToolFailed("edge-tts 没有返回音频", detail={"voice": chosen})
            # Everything downstream joins clips frame by frame with the
            # standard library, which reads WAV and nothing else.
            out_path.parent.mkdir(parents=True, exist_ok=True)
            ffmpeg.run(["-i", str(raw), "-ar", "22050", "-ac", "1", str(out_path)])
        except ToolFailed:
            raise
        except Exception as exc:  # noqa: BLE001 - network, endpoint, codec
            raise ToolFailed("edge-tts 合成失败", detail={"error": str(exc)[:200]}) from exc
        finally:
            raw.unlink(missing_ok=True)

        return audio_duration(out_path) or 0.0
