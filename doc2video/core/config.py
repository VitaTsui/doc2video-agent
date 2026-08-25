"""Runtime configuration, loaded from environment / .env."""

from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide settings. All keys are prefixed with ``D2V_`` except vendor keys."""

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="D2V_", extra="ignore", case_sensitive=False
    )

    # --- LLM ---
    # "mock" is the default on purpose: this service's contract is that the
    # caller writes the script, and an install that suddenly started calling a
    # model because a key happened to be in the environment would be a
    # surprise. The desktop app sets this explicitly; MCP callers never do.
    # Also accepts "auto" | "anthropic" | "openai" | "gemini" | "compatible".
    llm_provider: str = "mock"
    # Empty means "the provider's own default"; the compatible channel has no
    # default and must be told, since a gateway serves whatever it serves.
    llm_model: str = ""
    # Required by "compatible", optional elsewhere (a proxy in front of a vendor).
    llm_base_url: str = ""
    llm_max_tokens: int = 16000
    llm_effort: str = "high"  # anthropic only: low | medium | high | xhigh | max

    # --- Local CLI Agent as a model (agent-virtualization) ---
    # A machine with Claude Code or Codex installed already has a model on it.
    # The package wraps that CLI behind a bridge process; these point at it.
    agent_cli_path: str = ""  # explicit bin; empty means PATH, then the Node workspace
    agent_cli_config: str = ""  # its config file; empty generates a default one
    agent_cli_runtime: str = "claude-code"  # which CLI that default drives: claude-code | codex
    # A CLI Agent thinks for as long as it wants to; this is a ceiling, not a target.
    agent_cli_timeout: int = 900

    # Vendor keys keep their conventional names — a machine that already has
    # ANTHROPIC_API_KEY exported should not need it copied under a D2V_ alias.
    anthropic_api_key: str = Field(
        default="", validation_alias=AliasChoices("ANTHROPIC_API_KEY", "D2V_ANTHROPIC_API_KEY")
    )
    openai_api_key: str = Field(
        default="", validation_alias=AliasChoices("OPENAI_API_KEY", "D2V_OPENAI_API_KEY")
    )
    gemini_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "GEMINI_API_KEY", "GOOGLE_API_KEY", "D2V_GEMINI_API_KEY"
        ),
    )
    compatible_api_key: str = Field(
        default="", validation_alias=AliasChoices("D2V_COMPATIBLE_API_KEY")
    )

    # --- TTS ---
    tts_provider: str = "auto"
    tts_voice: str = ""
    # The engine's own comfortable pace, after two goes at speeding it up were
    # both heard as hurried (1.05, then 1.02). `say -r` is words a minute and
    # quantises coarsely — measured end to end on one page, 1.00 speaks 257
    # characters a minute, 1.02 speaks 277, 1.05 speaks 281 — so the step from
    # 1.00 is bigger than it looks and there is nothing in between to take.
    # The rest of what 「快」 means is the gaps, and those are ours to set: see
    # `voice.pause_comma`, `voice.pause_enum`, `voice.pause_sentence`.
    tts_speech_rate: float = 1.0
    # How many pages to speak, and how many scenes to draw, at once. Both are
    # per-page work that spends its time waiting on a subprocess, and doing
    # them one at a time was nineteen minutes of a forty-minute film. Zero
    # means as many as this machine can take.
    voice_workers: int = 0
    render_workers: int = 0
    narrate_workers: int = 0
    # Silence around each page's narration, so pages do not cut into one
    # another and nobody speaks over a slide that is still fading in. Part of
    # the scene's own clip, so subtitles stay inside the speech.
    #
    # Together they are the beat at a page turn, and it is the longest pause
    # in the film for a reason: the viewer has a new page to take in. Once the
    # engine's own trailing silence was trimmed the measured gap fell from
    # 2.39s to these two numbers alone, and a page turn stopped landing — one
    # page ran into the next. So they are set to what the measurement showed
    # was actually working, rather than to the smallest that reads as a pause.
    scene_lead_seconds: float = 1.1
    scene_tail_seconds: float = 1.0

    # --- Renderer / encoding ---
    renderer: str = "auto"
    # Explicit binary paths win over PATH and over the vendored wheel.
    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    video_width: int = 1920
    video_height: int = 1080
    video_fps: int = 30

    # --- Rollout ---
    # Per-flag rollout percentage, overriding the defaults in core/flags.py.
    # From the environment as JSON: D2V_FLAGS='{"llm_prefer_claude_code": 25}'
    flags: dict[str, int] = Field(default_factory=dict)

    # --- Jobs ---
    # A render saturates the CPU, so more than one at a time makes everything
    # slower rather than anything faster. Raise only on a machine with cores to
    # spare — and remember rendering shares them with whatever else runs there.
    max_concurrent_jobs: int = 1
    max_queued_jobs: int = 20
    max_upload_mb: int = 100

    # --- Storage ---
    storage_dir: Path = Path("./storage")
    # The Node workspace: the Remotion project and the agent-virtualization
    # bridge both live in its node_modules. Defaults to the copy in a source
    # checkout; a packaged install (the desktop app) points it at the runtime
    # it downloaded, since `renderer/` is not part of the Python wheel.
    node_dir: Path = Path(__file__).resolve().parents[2] / "renderer"

    # --- Server ---
    # Shared secret for every route, MCP included. Empty means no auth, which
    # is only safe on loopback — `serve` refuses to bind elsewhere without it.
    api_token: str = ""
    # Browser origins allowed to send that token. Same-origin by default.
    cors_origins: list[str] = Field(default_factory=list)
    mcp_enabled: bool = True
    # Host headers the MCP endpoint will answer to. The SDK rejects anything
    # else (DNS-rebinding protection), so a deployment behind a domain must
    # list that domain here or every MCP request comes back 421.
    mcp_allowed_hosts: list[str] = Field(default_factory=list)
    # Loopback by default: this is a single-user tool that renders local decks,
    # and every route either spends CPU or serves someone's slides. Exposing it
    # is a deliberate act — pass --host explicitly, with a token.
    host: str = "127.0.0.1"
    port: int = 8400
    log_level: str = "INFO"

    # --- Derived paths ---
    @property
    def projects_dir(self) -> Path:
        return self.storage_dir / "projects"

    @property
    def uploads_dir(self) -> Path:
        return self.storage_dir / "uploads"

    @property
    def render_work_dir(self) -> Path:
        """Scratch the renderers write into, kept out of the Node workspace.

        Remotion resolves browser-loadable assets from a ``public/`` directory
        and writes its image sequences somewhere; both used to land inside
        ``renderer/``. That is fine in a source checkout and impossible in an
        installed app, where the program directory is read-only. ``--public-dir``
        lets the location move, so it moves here.
        """
        return self.storage_dir / "render-work"

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
