"""VideoProject — the single source of truth.

The MP4 is one output of this object, not the asset itself. Every chat edit is a
patch against a VideoProject, and every render reads from it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from .document import DocumentModel
from .scene import Scene
from .telemetry import QualityReport, RunRecord
from .timeline import Timeline


def _now() -> datetime:
    return datetime.now(UTC)


class SourceType(StrEnum):
    PDF = "pdf"
    PPT = "ppt"
    PPTX = "pptx"


class ProjectStatus(StrEnum):
    CREATED = "created"
    PARSING = "parsing"
    WRITING = "writing"
    VOICING = "voicing"
    DIRECTING = "directing"
    RENDERING = "rendering"
    REVIEWING = "reviewing"
    READY = "ready"
    FAILED = "failed"


class Source(BaseModel):
    type: SourceType
    file: str = Field(description="Original filename")
    path: str = Field(description="Stored path relative to the project directory")
    page_count: int = 0


class VideoIntent(BaseModel):
    """Natural language goal, structured. Produced by the agent from one sentence."""

    audience: str = "通用受众"
    style: str = "professional"
    tone: str = "清晰、稳重"
    language: str = "zh"
    duration: int = Field(default=480, description="Target total duration in seconds")
    emphasis_pages: list[int] = Field(default_factory=list)
    skip_pages: list[int] = Field(default_factory=list)
    instructions: str = ""
    zoom_on_key_data: bool = True


class RenderState(BaseModel):
    renderer: str = ""
    status: str = "idle"
    output_path: str | None = None
    # scene_id -> content hash rendered last time; drives incremental re-render.
    rendered_scenes: dict[str, str] = Field(default_factory=dict)
    scene_clips: dict[str, str] = Field(default_factory=dict)
    last_render_at: datetime | None = None
    message: str = ""


class ReviewFinding(BaseModel):
    severity: str = "warning"
    kind: str = ""
    scene_id: str | None = None
    message: str = ""


class HistoryEntry(BaseModel):
    at: datetime = Field(default_factory=_now)
    message: str = ""
    actions: list[str] = Field(default_factory=list)
    changed_scenes: list[str] = Field(default_factory=list)


class VideoProject(BaseModel):
    project_id: str
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    status: ProjectStatus = ProjectStatus.CREATED

    source: Source
    intent: VideoIntent = Field(default_factory=VideoIntent)
    document: DocumentModel = Field(default_factory=DocumentModel)
    scenes: list[Scene] = Field(default_factory=list)
    timeline: Timeline = Field(default_factory=Timeline)
    assets: dict[str, str] = Field(default_factory=dict)
    render: RenderState = Field(default_factory=RenderState)
    review: list[ReviewFinding] = Field(default_factory=list)
    # Set by the review skill; the run that produced them is in `telemetry`.
    quality: QualityReport | None = None
    telemetry: RunRecord | None = None
    history: list[HistoryEntry] = Field(default_factory=list)

    # --- scene access -------------------------------------------------
    def scene(self, scene_id: str) -> Scene | None:
        for scene in self.scenes:
            if scene.scene_id == scene_id:
                return scene
        return None

    def scene_by_page(self, page_index: int) -> Scene | None:
        for scene in self.scenes:
            if scene.source_page == page_index:
                return scene
        return None

    def require_scene(self, scene_id: str) -> Scene:
        scene = self.scene(scene_id)
        if scene is None:
            raise KeyError(scene_id)
        return scene

    # --- render bookkeeping -------------------------------------------
    def dirty_scenes(self) -> list[Scene]:
        """Scenes whose content changed since the last successful render."""
        return [
            s
            for s in self.scenes
            if self.render.rendered_scenes.get(s.scene_id) != s.content_hash()
        ]

    def total_duration(self) -> float:
        return sum(s.duration for s in self.scenes)

    def touch(self) -> None:
        self.updated_at = _now()
