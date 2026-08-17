"""Runtime configuration, loaded from environment / .env."""

from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide settings. All keys are prefixed with ``D2V_`` except vendor keys."""

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="D2V_", extra="ignore", case_sensitive=False
    )

    # --- LLM / VLM ---
    # auto: API key first, then the local Claude Code CLI, then heuristics.
    # Also accepts "anthropic" | "claude_code" | "mock" to pin one path.
    llm_provider: str = "auto"
    llm_model: str = "claude-opus-5"
    llm_effort: str = "high"
    llm_max_tokens: int = 16000
    # claude_code provider only: an explicit binary wins over PATH, and a whole
    # deck's narration in one call can legitimately take minutes.
    claude_cli_path: str = ""
    claude_cli_timeout: int = 600

    # --- TTS ---
    tts_provider: str = "auto"
    tts_voice: str = ""
    tts_speech_rate: float = 1.0

    # --- Renderer / encoding ---
    renderer: str = "auto"
    # Explicit binary paths win over PATH and over the vendored wheel.
    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    video_width: int = 1920
    video_height: int = 1080
    video_fps: int = 30

    # --- Storage ---
    storage_dir: Path = Path("./storage")

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8400
    log_level: str = "INFO"

    # --- Derived paths ---
    @property
    def projects_dir(self) -> Path:
        return self.storage_dir / "projects"

    @property
    def uploads_dir(self) -> Path:
        return self.storage_dir / "uploads"

    def ensure_dirs(self) -> None:
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


def which(binary: str) -> str | None:
    """Locate an external binary, returning None when it is not installed."""
    return shutil.which(binary)


def dependency_report() -> dict[str, dict[str, object]]:
    """Report the availability of optional external binaries.

    Every entry is optional: the pipeline degrades instead of crashing, but the
    listed capability is unavailable while the binary is missing. ffmpeg and
    ffprobe report *where* they came from, since a vendored copy and a system
    install behave differently (see tools/media_binaries.py).
    """
    # Imported here to avoid a circular import at module load.
    from ..tools import media_binaries

    report: dict[str, dict[str, object]] = {}
    for binary, purpose in (
        (media_binaries.ffmpeg(), "编码、拼接、混音、封装；也是纯 ffmpeg 渲染器的依赖"),
        (media_binaries.ffprobe(), "音频时长探测（缺失时改用 ffmpeg 解析，不影响结果）"),
    ):
        report[binary.name] = {
            "available": binary.available,
            "path": binary.path,
            "source": binary.source,
            "purpose": purpose,
        }

    for name, purpose in {
        "node": "Remotion 渲染器所需",        "npx": "Remotion 渲染器所需",
        "soffice": "以原始样式渲染 PPT/PPTX 幻灯片（LibreOffice）",
        "say": "macOS 内置 TTS",
    }.items():
        path = which(name)
        report[name] = {
            "available": path is not None,
            "path": path,
            "source": "system" if path else "missing",
            "purpose": purpose,
        }
    return report


def filter_report() -> dict[str, dict[str, object]]:
    """Report optional ffmpeg filters the pipeline degrades without.

    Having ffmpeg is not the same as having every filter: builds differ in what
    they compile in, and a missing one costs a feature rather than the render.
    Which build is in use decides this, so it belongs next to the binary report
    an operator already reads.
    """
    from ..tools import media_binaries

    return {
        "drawtext": {
            "available": media_binaries.has_filter("drawtext"),
            "purpose": "烧录字幕（缺失时只跳过字幕，渲染照常完成）",
        }
    }
