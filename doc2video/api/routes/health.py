"""Health and capability reporting.

The dependency report is deliberately part of the API: which optional binaries
are present decides whether rendering, LibreOffice-quality slides or real TTS
are available, and an operator should not have to read logs to find out.
"""

from __future__ import annotations

from hashlib import sha1

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from ...core import inventory, prefs
from ...core.config import dependency_report, filter_report, get_settings
from ...core.errors import Doc2VideoError
from ...tools.llm import llm_status
from ...tools.llm.models import catalogue_payload
from ...tools.renderer import renderer_status
from ...tools.tts import TTSTool, piper_catalogue
from ...tools.tts import packs as voice_packs
from ...tools.tts.install import install_into_runtime


class VoiceChoiceIn(BaseModel):
    """The voice to use from now on. Empty hands the choice back to the machine."""

    voice: str = ""


class PiperVoiceIn(BaseModel):
    """Which published voice to download, by its key (`zh_CN-huayan-medium`)."""

    key: str


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


@router.put("/health/voices/current")
def choose_voice(body: VoiceChoiceIn) -> dict:
    """Pick the voice new videos start with.

    Written to the preferences file rather than to settings: settings are the
    environment the process was started with and are frozen for its life, so a
    voice kept there could only be changed by restarting the backend. Picking a
    voice should not restart anything.

    It applies to videos made after this, not to ones already made — a project
    carries the voice it was created with, so re-rendering an old video does
    not quietly change how it sounds.
    """
    settings = get_settings()
    chosen = body.voice.strip()
    if chosen:
        known = {
            voice["id"]
            for pack in voice_packs.catalogue(settings)
            if pack.installed
            for voice in pack.voices
        }
        if chosen not in known:
            raise HTTPException(
                status_code=400,
                detail={"code": "unknown_voice", "message": f"这台机器上没有这个音色：{chosen}"},
            )
    prefs.save(prefs.Preferences(voice=chosen), settings)
    return voice_packs.payload(settings)


# One sentence of the kind these videos are made of: a page being introduced.
# Long enough to hear a voice's pace and where it breathes, short enough that
# waiting for it does not feel like a request.
SAMPLE = "这一页讲的是系统架构，我们从最上面一层看起。"


@router.get("/health/voices/preview")
def preview_voice(voice: str = "") -> Response:
    """Say one sentence in this voice, so it can be heard before it is chosen.

    Synthesised by the engine that owns the voice, directly — not through the
    path a render takes. A render that cannot reach the network falls back to a
    local voice and says so, which is right for a video and wrong here: the
    whole question being asked is 「这个音色是什么样」, and answering it in a
    different voice is answering a different question.

    Cached on disk by voice: the second press should be instant, and eight
    voices auditioned in a row should not be eight network round trips.

    A GET that an `<audio src>` can point straight at, rather than bytes fetched
    and wrapped in a blob URL: the app's own CSP allows media from the backend
    and not from `blob:`, so the blob played in a browser and failed inside the
    window it was built for — 「Failed to load because no supported source was
    found」. The token rides in the query for the same reason it does for the
    finished video: a media element cannot send a header.
    """
    settings = get_settings()
    tool = TTSTool(settings)
    voice = voice.strip() or tool.voice
    engine = tool._engine_for(voice)  # noqa: SLF001 - the same lookup a render does

    target = settings.storage_dir / "previews" / f"{sha1(voice.encode()).hexdigest()}.wav"
    if not target.exists():
        try:
            engine.synthesize(SAMPLE, target, voice=voice, rate=engine.natural_rate)
        except Doc2VideoError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(
                status_code=502,
                detail={"code": "preview_failed", "message": f"{engine.name} 试听失败：{exc}"},
            ) from exc
    return Response(target.read_bytes(), media_type="audio/wav")


@router.get("/health/voices/piper")
def piper_voices(q: str = "", limit: int = 40) -> dict:
    """The published Piper voices, searchable.

    Its own route rather than part of the pack list: 174 voices is a thing you
    look through, and the pack list is a thing you read. The index is cached on
    disk after the first look, so this answers offline and instantly.
    """
    return piper_catalogue.search(q, limit=limit, settings=get_settings())


@router.post("/health/voices/piper/install")
def install_piper_voice(body: PiperVoiceIn) -> dict:
    """Download one voice into the voices directory.

    Synchronous, like the pack install next to it, and for the same reason: a
    request that returns before the file is there leaves the window saying
    「已装」 about something that is not. The size is on the button first.
    """
    settings = get_settings()
    try:
        piper_catalogue.install(body.key, settings)
    except Doc2VideoError as exc:
        raise HTTPException(
            status_code=502, detail={"code": "download_failed", "message": str(exc)}
        ) from exc
    return piper_catalogue.search(body.key, limit=1, settings=settings)


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


@router.get("/health/plugins")
def plugins() -> dict:
    """What this build can do, step by step, and what works on this machine.

    The window needs both halves and can derive neither: what a step is for is
    written next to the code that does it, and whether its tools are usable
    here is something only this process can answer.
    """
    return inventory.report(get_settings())


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
