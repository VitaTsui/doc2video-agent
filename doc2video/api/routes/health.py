"""Health and capability reporting.

The dependency report is deliberately part of the API: which optional binaries
are present decides whether rendering, LibreOffice-quality slides or real TTS
are available, and an operator should not have to read logs to find out.
"""

from __future__ import annotations

from fastapi import APIRouter

from ...core.config import dependency_report, filter_report, get_settings
from ...tools.llm import llm_status
from ...tools.llm.models import catalogue_payload
from ...tools.renderer import renderer_status
from ...tools.tts import TTSTool

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/health/capabilities")
def capabilities() -> dict:
    settings = get_settings()
    return {
        "llm": llm_status(settings),
        "tts": {"provider": TTSTool(settings).provider_name},
        "renderers": renderer_status(settings),
        "binaries": dependency_report(),
        "filters": filter_report(),
        "video": {
            "width": settings.video_width,
            "height": settings.video_height,
            "fps": settings.video_fps,
        },
    }


@router.get("/health/models")
def models() -> dict:
    """Providers this build can talk to, and a starting list of model ids.

    The list is a convenience, never a gate: model ids change faster than a
    shipped app updates, so any id may be typed in by hand and is passed to the
    provider verbatim. ``needs_base_url`` is what a settings form has to know —
    the compatible channel is an address plus a key, not a vendor.
    """
    return {
        "providers": [
            {
                "id": "agent_cli",
                "label": "本机 CLI Agent",
                "needs_key": False,
                "needs_base_url": False,
                "note": "装了 Claude Code 或 Codex 就能用，不要 Key",
            },
            {"id": "anthropic", "label": "Anthropic", "needs_key": True, "needs_base_url": False},
            {"id": "openai", "label": "OpenAI", "needs_key": True, "needs_base_url": False},
            {"id": "gemini", "label": "Google Gemini", "needs_key": True, "needs_base_url": False},
            {
                "id": "compatible",
                "label": "OpenAI 兼容通道",
                "needs_key": True,
                "needs_base_url": True,
                "note": "DeepSeek / 通义 / 月之暗面 / 自建网关",
            },
        ],
        "models": catalogue_payload(),
    }
