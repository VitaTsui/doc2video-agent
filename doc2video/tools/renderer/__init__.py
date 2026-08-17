"""Renderer registry. ``VideoRenderer`` picks an adapter and owns nothing else."""

from __future__ import annotations

from ...core.config import Settings, get_settings
from ...core.errors import DependencyMissing
from ...core.logging import get_logger
from .base import PlanAction, PlanArea, PlanSubtitle, RendererAdapter, ScenePlan
from .ffmpeg_adapter import FFmpegAdapter
from .genvideo import GenerativeVideoAdapter
from .remotion import RemotionAdapter

log = get_logger(__name__)

# Order matters: Remotion is the reference renderer, ffmpeg the dependable fallback.
ADAPTER_ORDER: list[type[RendererAdapter]] = [RemotionAdapter, FFmpegAdapter]

REGISTRY: dict[str, type[RendererAdapter]] = {
    RemotionAdapter.name: RemotionAdapter,
    FFmpegAdapter.name: FFmpegAdapter,
    GenerativeVideoAdapter.name: GenerativeVideoAdapter,
}


def select_adapter(settings: Settings | None = None) -> RendererAdapter:
    settings = settings or get_settings()
    preference = settings.renderer

    if preference != "auto":
        adapter_cls = REGISTRY.get(preference)
        if adapter_cls is None:
            raise DependencyMissing(f"未知的渲染器：{preference}")
        adapter = adapter_cls()
        if not adapter.available():
            raise DependencyMissing(
                f"渲染器 {preference} 不可用：{adapter.unavailable_reason()}",
                detail={"renderer": preference},
            )
        return adapter

    reasons: dict[str, str] = {}
    for adapter_cls in ADAPTER_ORDER:
        adapter = adapter_cls()
        if adapter.available():
            log.info("使用渲染器：%s", adapter.name)
            return adapter
        reasons[adapter.name] = adapter.unavailable_reason()

    raise DependencyMissing("没有可用的渲染器", detail={"reasons": reasons})


def renderer_status() -> dict[str, dict[str, object]]:
    status: dict[str, dict[str, object]] = {}
    for name, adapter_cls in REGISTRY.items():
        adapter = adapter_cls()
        ok = adapter.available()
        status[name] = {"available": ok, "reason": "" if ok else adapter.unavailable_reason()}
    return status


__all__ = [
    "FFmpegAdapter",
    "GenerativeVideoAdapter",
    "PlanAction",
    "PlanArea",
    "PlanSubtitle",
    "RemotionAdapter",
    "RendererAdapter",
    "ScenePlan",
    "renderer_status",
    "select_adapter",
]
