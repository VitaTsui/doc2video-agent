"""Renderer adapter contract and the scene render plan.

The agent emits one unified director DSL; adapters translate it. Nothing above
this file knows whether Remotion, ffmpeg or a generative-video service produced
the frames (方案 §11).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ...schemas import ActionType

# Fraction of frame height reserved for subtitles. Part of the renderer contract:
# every adapter draws subtitles inside this band, and the layout skill keeps
# highlights out of it, so the two never overlap.
SUBTITLE_SAFE_BOTTOM = 0.12


class PlanArea(BaseModel):
    """Target region normalized to 0..1 of the frame."""

    x: float
    y: float
    w: float
    h: float


class PlanAction(BaseModel):
    """A director action with times relative to the start of its own scene."""

    type: ActionType
    start: float
    end: float
    effect: str = ""
    target: str | None = None
    area: PlanArea | None = None
    params: dict = Field(default_factory=dict)


class PlanSubtitle(BaseModel):
    start: float
    end: float
    text: str


class ScenePlan(BaseModel):
    """Everything a renderer needs to produce one scene's frames."""

    scene_id: str
    duration: float
    width: int
    height: int
    fps: int
    # Absolute paths — renderers run in their own working directories.
    image: str | None = None
    video: str | None = None
    audio: str | None = None
    actions: list[PlanAction] = Field(default_factory=list)
    subtitles: list[PlanSubtitle] = Field(default_factory=list)
    transition_in: str = "fade"
    transition_duration: float = 0.4

    @property
    def total_frames(self) -> int:
        return max(1, int(round(self.duration * self.fps)))


class RendererAdapter:
    """Base adapter. ``render_scene`` must be deterministic for a given plan."""

    name = "base"

    def available(self) -> bool:
        return False

    def unavailable_reason(self) -> str:
        return "未实现"

    def render_scene(self, plan: ScenePlan, out_path: Path) -> Path:
        raise NotImplementedError
