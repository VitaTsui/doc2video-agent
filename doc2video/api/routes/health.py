"""Health and capability reporting.

The dependency report is deliberately part of the API: which optional binaries
are present decides whether rendering, LibreOffice-quality slides or real TTS
are available, and an operator should not have to read logs to find out.
"""

from __future__ import annotations

from fastapi import APIRouter

from ...core.config import dependency_report, filter_report, get_settings
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
        "tts": {"provider": TTSTool(settings).provider_name},
        "renderers": renderer_status(),
        "binaries": dependency_report(),
        "filters": filter_report(),
        "video": {
            "width": settings.video_width,
            "height": settings.video_height,
            "fps": settings.video_fps,
        },
    }
