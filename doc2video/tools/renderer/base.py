"""Renderer adapter contract and the scene render plan.

The agent emits one unified director DSL; adapters translate it. Nothing above
this file knows whether Remotion, ffmpeg or a generative-video service produced
the frames (方案 §11).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, Field

from ...schemas import ActionType

# Fraction of frame height reserved for subtitles. Part of the renderer contract:
# every adapter draws subtitles inside this band, and the layout skill keeps
# highlights out of it, so the two never overlap.
SUBTITLE_SAFE_BOTTOM = 0.12

# Gap between the bottom of the caption box and the bottom of the frame, as a
# fraction of frame *height*. It travels in the plan rather than staying a
# constant here because the adapters cannot otherwise agree on it: Remotion's
# CSS `paddingBottom: "6%"` resolved against the frame's width, so the caption
# drifted with the aspect ratio and ignored this file entirely. Being in the
# plan also puts it inside the render fingerprint, so moving the caption
# re-renders the clips instead of leaving the old position burned in.
SUBTITLE_BOTTOM_MARGIN = 0.03


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


class PlanChart(BaseModel):
    """A chart to draw live, over the region the flat page already shows it in.

    The page image already contains this chart — it was rebuilt from the same
    numbers by the same component when the slide was rasterised. Drawing it
    again on top is not a second chart; it is the same one, animated, and it
    settles into exactly the pixels underneath it. That is what makes the
    animation safe: when it finishes there is nothing to disagree with.
    """

    area: PlanArea
    # When the drawing happens. Tied to the action that pointed at it — a chart
    # animates because the narrator is talking about it, not because it exists.
    start: float
    grow: float = 0.9
    kind: str = "column"
    title: str = ""
    categories: list[str] = Field(default_factory=list)
    series: list[dict] = Field(default_factory=list)
    # What to paint behind the growing chart, sampled from the page image at
    # the chart's own corners. Without it the printed chart shows through at
    # full height while the live one is still growing, and the bars look cut
    # off rather than rising. Sampled rather than assumed white: a deck is
    # entitled to a dark slide.
    backdrop: str = "#ffffff"


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
    charts: list[PlanChart] = Field(default_factory=list)
    subtitles: list[PlanSubtitle] = Field(default_factory=list)
    subtitle_margin: float = SUBTITLE_BOTTOM_MARGIN
    transition_in: str = "fade"
    transition_duration: float = 0.4

    @property
    def total_frames(self) -> int:
        return max(1, int(round(self.duration * self.fps)))

    def fingerprint(self) -> str:
        """Identity of the frames this plan produces, for incremental render.

        The plan is the renderer's whole input, so hashing it is the only
        version of "has this scene changed" that cannot go stale: hashing the
        *scene* instead misses everything added on the way here — subtitles are
        split from the narration by the layout skill, so re-wording the split
        rules leaves every scene looking unchanged and silently reuses clips
        with the old captions burned in.

        Media paths are hashed by filename — the same project rendered from a
        moved storage directory is the same video.
        """
        payload = self.model_dump(mode="json", exclude={"image", "video", "audio"})
        payload["media"] = [Path(p).name for p in (self.image, self.video, self.audio) if p]
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class RendererAdapter:
    """Base adapter. ``render_scene`` must be deterministic for a given plan."""

    name = "base"

    def available(self) -> bool:
        return False

    def unavailable_reason(self) -> str:
        return "未实现"

    def render_scene(self, plan: ScenePlan, out_path: Path) -> Path:
        raise NotImplementedError
