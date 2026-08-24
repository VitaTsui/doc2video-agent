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
    # Whether that number came from the person or from this line. It decides
    # whether the script may be cut to fit it: 「八分钟」 is a promise to keep,
    # and a default nobody chose is not. Measured on a 30-page deck — naming
    # each of its 208 blocks in one short sentence takes 17 minutes, so the
    # 480-second default was quietly deciding that half the deck goes unsaid.
    duration_stated: bool = Field(
        default=False, description="True when the requested duration came from the user"
    )
    emphasis_pages: list[int] = Field(default_factory=list)
    skip_pages: list[int] = Field(default_factory=list)
    instructions: str = ""
    zoom_on_key_data: bool = True
    # Which voice, and how fast. Here rather than in settings because they are
    # a property of this video, not of the machine: settings are frozen for
    # the life of the process (`get_settings` is cached), so a voice kept
    # there could only be changed by restarting the backend — while everything
    # else about a video can be changed by saying so. Empty means "whatever
    # the machine is configured with".
    voice: str = ""
    speech_rate: float = 0.0
    # How to say a term this deck uses, when the engine gets it wrong and the
    # built-in list does not know about it. Keyed by the term as written; the
    # caption keeps the written form either way.
    pronunciation: dict[str, str] = Field(default_factory=dict)


class RenderState(BaseModel):
    renderer: str = ""
    status: str = "idle"
    output_path: str | None = None
    # scene_id -> the ScenePlan fingerprint rendered last time; a scene whose
    # plan still hashes the same keeps its clip instead of re-encoding.
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
    def total_duration(self) -> float:
        return sum(s.duration for s in self.scenes)

    def touch(self) -> None:
        self.updated_at = _now()
