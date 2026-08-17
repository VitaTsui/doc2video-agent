"""Timeline Model — answers "what happens at second N".

Layer 3, and the only thing a renderer adapter is allowed to read. It is fully
flattened and absolute-timed so that Remotion, ffmpeg or any future adapter
produce identical output from it.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from .document import BBox
from .scene import ActionType


class TrackKind(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLE = "subtitle"
    ACTION = "action"


class VideoClip(BaseModel):
    scene_id: str
    start: float
    end: float
    asset: str
    kind: str = "slide"
    transition_in: str = "fade"
    transition_duration: float = 0.4


class AudioClip(BaseModel):
    scene_id: str
    start: float
    end: float
    asset: str
    gain_db: float = 0.0


class SubtitleCue(BaseModel):
    start: float
    end: float
    text: str
    scene_id: str


class ActionCue(BaseModel):
    """An absolute-timed director action with geometry already resolved.

    ``area`` is normalized 0..1 against the video frame, so the renderer needs
    no knowledge of the source document's page size.
    """

    start: float
    end: float
    type: ActionType
    scene_id: str
    target: str | None = None
    effect: str = ""
    area: BBox | None = None
    params: dict = Field(default_factory=dict)


class Timeline(BaseModel):
    fps: int = 30
    width: int = 1920
    height: int = 1080
    duration: float = 0.0
    video: list[VideoClip] = Field(default_factory=list)
    audio: list[AudioClip] = Field(default_factory=list)
    subtitles: list[SubtitleCue] = Field(default_factory=list)
    actions: list[ActionCue] = Field(default_factory=list)

    @property
    def total_frames(self) -> int:
        return int(round(self.duration * self.fps))

    def scene_window(self, scene_id: str) -> tuple[float, float] | None:
        for clip in self.video:
            if clip.scene_id == scene_id:
                return clip.start, clip.end
        return None
