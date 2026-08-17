"""Scene Model — answers "how is this stretch of video told".

Layer 2 of the three intermediate models, and the Agent's unit of edit: a chat
instruction like "page 7 is too long, cut it to 20 seconds" mutates exactly one
Scene and re-renders only that scene.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, Field


class ActionType(StrEnum):
    ZOOM = "zoom"
    HIGHLIGHT = "highlight"
    POINTER = "pointer"
    PAN = "pan"
    RESET = "reset"
    TRANSITION = "transition"
    BROLL = "broll"


class VisualType(StrEnum):
    SLIDE = "slide"
    IMAGE = "image"
    GENERATED_VIDEO = "generated_video"
    EXISTING_VIDEO = "existing_video"


class NarrationSegment(BaseModel):
    """A sentence-sized chunk of narration, bound to the elements it talks about.

    The binding is what makes the director deterministic: the model decides
    *what* is being talked about, the timing comes from TTS timestamps.
    """

    id: str
    text: str
    # Element ids on the scene's source page that this sentence refers to.
    element_refs: list[str] = Field(default_factory=list)
    emphasis: bool = False
    # Filled in after TTS, relative to scene start (seconds).
    start: float = 0.0
    end: float = 0.0


class DirectorAction(BaseModel):
    """A camera / attention instruction at a precise time within the scene."""

    at: float = Field(description="Seconds from scene start")
    type: ActionType
    target: str | None = Field(default=None, description="Element id, when applicable")
    effect: str = ""
    duration: float = 2.0
    params: dict = Field(default_factory=dict)


class SceneVisual(BaseModel):
    type: VisualType = VisualType.SLIDE
    # Path relative to the project directory (rendered slide, image, b-roll clip).
    asset: str | None = None
    source_page: int | None = None


class SceneAudio(BaseModel):
    path: str | None = None
    duration: float = 0.0
    voice: str = ""
    provider: str = ""
    # Fingerprint of the text/voice this clip was synthesized from — lets the
    # voice skill skip scenes whose narration did not change (partial re-TTS).
    text_hash: str = ""


class Scene(BaseModel):
    scene_id: str
    source_page: int | None = None
    title: str = ""
    narration: str = ""
    segments: list[NarrationSegment] = Field(default_factory=list)
    duration: float = 0.0
    visual: SceneVisual = Field(default_factory=SceneVisual)
    actions: list[DirectorAction] = Field(default_factory=list)
    audio: SceneAudio = Field(default_factory=SceneAudio)
    notes: str = ""

    def content_hash(self) -> str:
        """Fingerprint of everything that affects this scene's rendered frames.

        Incremental render compares this against the hash recorded at last
        render; audio path is included because re-voicing changes the output.
        """
        payload = {
            "narration": self.narration,
            "duration": round(self.duration, 3),
            "visual": self.visual.model_dump(),
            "actions": [a.model_dump() for a in self.actions],
            "audio": self.audio.path,
            "segments": [
                {"t": s.text, "s": round(s.start, 3), "e": round(s.end, 3)} for s in self.segments
            ],
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:16]
