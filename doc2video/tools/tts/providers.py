"""Concrete TTS providers.

``macos_say`` gives real speech with zero setup on a Mac; ``silent`` guarantees
the pipeline produces a correctly-timed track anywhere. Cloud providers plug in
by subclassing TTSProvider — nothing above this file needs to change.
"""

from __future__ import annotations

import struct
import subprocess
import wave
from pathlib import Path

from ...core.config import which
from ...core.errors import ToolFailed
from ...core.logging import get_logger
from .base import TTSProvider, audio_duration, estimate_duration
from .edge import EdgeProvider
from .kokoro import KokoroProvider
from .piper import PiperProvider

log = get_logger(__name__)

SAMPLE_RATE = 22050


class MacOSSayProvider(TTSProvider):
    """Uses the built-in macOS `say` binary, writing 16-bit PCM WAVE."""

    name = "macos_say"
    # Measured over a 30-page deck with Tingting: 4.75 characters a second.
    chars_per_second = 4.75

    def available(self) -> bool:
        return which("say") is not None

    def voices(self) -> list[str]:
        say = which("say")
        if say is None:
            return []
        try:
            listed = subprocess.run(
                [say, "-v", "?"], capture_output=True, text=True, timeout=10, check=True
            ).stdout
        except (subprocess.SubprocessError, OSError):
            return []
        names: list[str] = []
        for line in listed.splitlines():
            if "zh_CN" not in line:
                continue
            # The *whole* name, parentheses included. Most of these voices exist
            # in several languages under one first name, and `say -v Flo` picks
            # whichever Flo is the default — an English one, which writes a
            # 0.02-second file for a page of Chinese. Silent output, exit code
            # zero, and a scene with no narration in it. The listing's full
            # name is the only one that selects the Mandarin voice:
            #
            #     say -v "Flo (中文（中国大陆）)"  → 3.89s
            #     say -v Flo                      → 0.02s
            name = line.split("zh_CN")[0].strip()
            if name and name not in names:
                names.append(name)
        return names

    def synthesize(self, text: str, out_path: Path, *, voice: str = "", rate: float = 1.0) -> float:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # `say -r` is words per minute; 175 is roughly its natural pace.
        words_per_minute = int(175 * max(rate, 0.5))
        cmd = ["say", "-o", str(out_path), "--data-format=LEI16@22050", "-r", str(words_per_minute)]
        if voice:
            cmd += ["-v", voice]
        cmd += ["--", text]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        except subprocess.CalledProcessError as exc:
            raise ToolFailed(
                "macOS say 合成失败", detail={"stderr": exc.stderr.decode("utf-8", "ignore")[:400]}
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ToolFailed("macOS say 合成超时") from exc

        duration = audio_duration(out_path)
        if duration is None:
            raise ToolFailed("无法读取合成音频时长", detail={"path": str(out_path)})
        return duration


class SilentProvider(TTSProvider):
    """Writes silence of the estimated duration.

    Not a toy: it keeps timeline, subtitles and director cues exercised and
    correctly timed on machines with no TTS available, so renders stay valid.
    """

    name = "silent"

    def available(self) -> bool:
        return True

    def synthesize(self, text: str, out_path: Path, *, voice: str = "", rate: float = 1.0) -> float:  # noqa: ARG002
        duration = estimate_duration(text, rate)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        frame_count = int(duration * SAMPLE_RATE)
        with wave.open(str(out_path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(SAMPLE_RATE)
            handle.writeframes(struct.pack("<h", 0) * frame_count)
        return duration


# What ``auto`` tries, in order. `say` first where it exists: it is instant,
# needs no model on disk, and is the reason macOS never noticed this project had
# no cross-platform voice. Piper is what every other platform gets — before it,
# a run off macOS produced a correctly timed, correctly subtitled, silent video.
# Best first — and "best" turned out to need an ear, not only numbers.
#
# Kokoro varies its pauses five times as much as `say` (0.66 against 0.13),
# which is the measurable half of sounding like a person rather than a
# punctuation table, and on that basis this list had it first. Listening
# settled it the other way for Mandarin narration: it carries an accent, and
# for explaining a deck an even, accentless delivery beats a livelier one that
# sounds foreign. The metric was necessary and not sufficient — it cannot hear
# an accent, and nothing here could have.
#
# So `say` leads where it exists. Kokoro sits ahead of Piper, which is where it
# earns its place: on Windows and Linux there is no `say`, and the choice is
# between a neural voice with an accent and the one shipped Piper model.
AUTO_ORDER: tuple[type[TTSProvider], ...] = (
    MacOSSayProvider,
    KokoroProvider,
    PiperProvider,
    SilentProvider,
)


def resolve_provider(preference: str) -> TTSProvider:
    """Pick a provider by name, or the best available one for ``auto``."""
    registry: dict[str, type[TTSProvider]] = {
        MacOSSayProvider.name: MacOSSayProvider,
        KokoroProvider.name: KokoroProvider,
        EdgeProvider.name: EdgeProvider,
        PiperProvider.name: PiperProvider,
        SilentProvider.name: SilentProvider,
        "mock": SilentProvider,
    }

    if preference != "auto":
        provider_cls = registry.get(preference)
        if provider_cls is None:
            log.warning("未知的 TTS provider '%s'，回退到 auto", preference)
        else:
            provider = provider_cls()
            if provider.available():
                return provider
            reason = getattr(provider, "unavailable_reason", lambda: "")()
            log.warning(
                "TTS provider '%s' 当前不可用%s，回退到 auto",
                preference,
                f"（{reason}）" if reason else "",
            )

    for provider_cls in AUTO_ORDER:
        provider = provider_cls()
        if provider.available():
            return provider
    return SilentProvider()
