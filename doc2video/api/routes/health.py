"""Health and capability reporting.

The dependency report is deliberately part of the API: which optional binaries
are present decides whether rendering, LibreOffice-quality slides or real TTS
are available, and an operator should not have to read logs to find out.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...core.config import dependency_report, filter_report, get_settings
from ...tools.llm import llm_status
from ...tools.llm.models import catalogue_payload
from ...tools.renderer import renderer_status
from ...tools.tts import TTSTool
from ...tools.tts import packs as voice_packs
from ...tools.tts.install import install_into_runtime


class VoicePackIn(BaseModel):
    pack: str


router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/health/voices")
def voices() -> dict:
    """Which voices this machine can speak with, and what the rest would cost.

    Part of the API because the window has to offer a choice and cannot work
    out on its own which engines are installed — and because "installed" is not
    a fixed property of a build: a pack can be added later, from here.
    """
    return voice_packs.payload(get_settings())


@router.post("/health/voices/install")
def install_voice(body: VoicePackIn) -> dict:
    """Put a voice pack into the runtime this backend is running in.

    Synchronous: the pack a person is most likely to choose is a megabyte, and
    a request that returns before the download finishes leaves the window
    saying "installed" about something that is not. The heavy one says its
    size on the button, so the wait is not a surprise.
    """
    packs = {pack.id: pack for pack in voice_packs.catalogue(get_settings())}
    pack = packs.get(body.pack)
    if pack is None or not pack.packages:
        raise HTTPException(
            status_code=400,
            detail={"code": "unknown_pack", "message": f"没有可安装的语音包：{body.pack}"},
        )
    if pack.installed:
        return {"installed": True, "voices": pack.voices}

    if (failure := install_into_runtime(pack.packages)) is not None:
        raise HTTPException(
            status_code=500, detail={"code": "install_failed", "message": failure[:400]}
        )
    # Ask again rather than assume: the install can succeed and the import
    # still fail, and this route's whole job is to report which of those it is.
    fresh = {item.id: item for item in voice_packs.catalogue(get_settings())}[body.pack]
    if not fresh.installed:
        raise HTTPException(
            status_code=500,
            detail={"code": "install_failed", "message": "装完了但仍然不可用"},
        )
    return {"installed": True, "voices": fresh.voices}


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
                "note": "装了并登录过 Claude Code 或 Codex 就能用，不要 Key",
                "model_label": "用哪个 CLI",
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
