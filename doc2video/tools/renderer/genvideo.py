"""Generative-video adapter (B-roll only).

Deliberately unimplemented and deliberately present: it fixes the boundary the
project depends on — information-bearing frames come from deterministic
rendering, generative video only ever supplies decorative B-roll (方案 §12、§20).
Wire a provider in here and nothing else in the pipeline changes.
"""

from __future__ import annotations

from pathlib import Path

from ...core.config import Settings
from .base import RendererAdapter, ScenePlan


class GenerativeVideoAdapter(RendererAdapter):
    name = "genvideo"

    def __init__(self, settings: Settings | None = None) -> None:
        # Accepted for a uniform constructor across adapters; this one needs
        # nothing from settings.
        self.settings = settings

    def available(self) -> bool:
        # No provider is configured by default; MVP does not ship one.
        return False

    def unavailable_reason(self) -> str:
        return "未配置生成式视频服务（仅用于 B-roll / 概念动画，不承载信息型画面）"

    def render_scene(self, plan: ScenePlan, out_path: Path) -> Path:
        raise NotImplementedError(
            "GenerativeVideoAdapter 未接入服务商；请勿用它渲染承载数据的画面"
        )
