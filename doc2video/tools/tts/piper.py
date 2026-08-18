"""Neural speech that runs anywhere, from a model file on disk.

Until this existed the pipeline had real speech on macOS and silence everywhere
else: `say` is the only provider that was ever implemented, and off macOS every
run fell through to `SilentProvider` — correctly timed, correctly subtitled, and
mute. That is the single reason this project could not ship for Windows or
Linux.

Piper is a small ONNX model plus its phonemiser, both installed with the Python
package, so there is no separate binary to find and nothing to download at
render time. The voice model is the one thing that lives outside the wheel
(~61MB); it is looked up rather than fetched, because a render that pauses to
download 61MB is a render that appears to have hung.

**macOS keeps using `say`.** Not preference — piper-tts 1.7.0's macOS wheel has
its espeak data path compiled in as the build machine's, so phonemisation fails
there with a path under `/Users/runner/…` that no user will ever have. The Linux
wheel is verified working; `say` covers macOS anyway, at no download.
"""

from __future__ import annotations

import wave
from pathlib import Path

from ...core.config import Settings
from ...core.errors import ToolFailed
from ...core.logging import get_logger
from .base import TTSProvider

log = get_logger(__name__)

# What `doc2video voices` installs when asked for nothing in particular. Medium
# is the size worth defaulting to: low is audibly robotic, high costs several
# times the compute for a difference most listeners do not notice under a slide.
DEFAULT_VOICE = "zh_CN-huayan-medium"


class PiperProvider(TTSProvider):
    name = "piper"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    # -- discovery -----------------------------------------------------
    def available(self) -> bool:
        return self._import_error() is None and self.voice_path() is not None

    def unavailable_reason(self) -> str:
        if (error := self._import_error()) is not None:
            return f"未安装 piper-tts（{error}）"
        return (
            f"没有找到语音模型。执行 `doc2video voices` 下载（约 61MB），"
            f"或把 .onnx 放进 {self._voices_dir()}"
        )

    @staticmethod
    def _import_error() -> str | None:
        try:
            import piper  # noqa: F401
        except Exception as exc:  # ImportError, or a broken native extension
            return str(exc)[:120]
        return None

    def _voices_dir(self) -> Path:
        from ...core.config import get_settings

        settings = self._settings or get_settings()
        return settings.storage_dir / "voices"

    def voice_path(self) -> Path | None:
        """The model to speak with: an explicit path, a name, or whatever is there.

        Falling back to "whatever is there" matters for the packaged app, where
        the runtime ships one voice and nobody has configured anything.
        """
        from ...core.config import get_settings

        settings = self._settings or get_settings()
        wanted = settings.tts_voice.strip()

        if wanted.endswith(".onnx"):
            path = Path(wanted).expanduser()
            return path if path.exists() else None

        directory = self._voices_dir()
        if wanted:
            named = directory / f"{wanted}.onnx"
            return named if named.exists() else None

        preferred = directory / f"{DEFAULT_VOICE}.onnx"
        if preferred.exists():
            return preferred
        return next(iter(sorted(directory.glob("*.onnx"))), None)

    # -- synthesis -----------------------------------------------------
    def synthesize(self, text: str, out_path: Path, *, voice: str = "", rate: float = 1.0) -> float:
        from piper import SynthesisConfig

        model = Path(voice) if voice.endswith(".onnx") else self.voice_path()
        if model is None or not model.exists():
            raise ToolFailed(
                "没有可用的 Piper 语音模型", detail={"hint": self.unavailable_reason()}
            )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            speaker = _load(str(model))
            with wave.open(str(out_path), "wb") as handle:
                speaker.synthesize_wav(
                    text,
                    handle,
                    # length_scale stretches time, so it is the reciprocal of
                    # the rate everything else in this project speaks in.
                    syn_config=SynthesisConfig(length_scale=1.0 / max(rate, 0.1)),
                )
        except ToolFailed:
            raise
        except Exception as exc:
            raise ToolFailed("Piper 合成失败", detail={"error": str(exc)[:200]}) from exc

        with wave.open(str(out_path), "rb") as handle:
            framerate = handle.getframerate()
            return handle.getnframes() / framerate if framerate else 0.0


_LOADED: dict[str, object] = {}


def _load(model: str):
    """Keep loaded voices around — loading costs seconds, speaking costs less.

    A deck is a dozen calls with the same voice; reloading the model for each
    one would dominate the time the whole voicing stage takes.
    """
    if model not in _LOADED:
        from piper import PiperVoice

        _LOADED[model] = PiperVoice.load(model)
    return _LOADED[model]


def download_voice(name: str, directory: Path) -> Path:
    """Fetch one voice model. Deliberately not called during a render."""
    from piper.download_voices import download_voice as fetch

    directory.mkdir(parents=True, exist_ok=True)
    log.info("正在下载语音模型 %s（约 61MB）…", name)
    fetch(name, directory)
    return directory / f"{name}.onnx"
