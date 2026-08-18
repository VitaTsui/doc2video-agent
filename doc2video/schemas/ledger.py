"""The account of how one video got made, as something a person can read.

Telemetry already recorded how long each stage took and what degraded. That
answers "is it slow" and "did it quietly get worse" — operator questions. It
does not answer the question someone actually has while watching a render:
*what did that step produce, and can I look at it?*

So every entry names its outputs, and an output is either something on disk
(a page render, an audio clip, a scene clip) or something small enough to read
inline (the narration for a page, the review findings). Paths are
project-relative and therefore already serveable — the same asset route the
window uses for slide thumbnails.

This is also where the agent writes its own reasoning: a decision to redo page
7 sits in the same sequence as the render it caused, because to the person
reading it those are one story, not two.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(UTC)


class ArtifactKind(StrEnum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    # Held inline rather than on disk: narration, findings, a plan.
    TEXT = "text"
    JSON = "json"


class Artifact(BaseModel):
    """One thing a step produced, and how to look at it."""

    label: str
    kind: ArtifactKind
    # Project-relative, for anything on disk. Empty for inline artifacts.
    path: str = ""
    # The content itself, for artifacts too small to be worth a file.
    text: str = ""
    # For a scene-scoped artifact, so the UI can group by page.
    scene_id: str = ""


class EntryKind(StrEnum):
    STAGE = "stage"
    # The agent chose to do something, and why.
    DECISION = "decision"
    # A step did its lesser job because it could not do its better one.
    DEGRADATION = "degradation"
    NOTE = "note"


class LedgerEntry(BaseModel):
    seq: int
    kind: EntryKind
    name: str
    detail: str = ""
    status: str = "ok"  # ok | failed | skipped
    duration_s: float = 0.0
    at: datetime = Field(default_factory=_now)
    artifacts: list[Artifact] = Field(default_factory=list)
    # Which run this belongs to, so a project's history stays separable after
    # several edits — "the first render" and "after I shortened page 3".
    run_id: str = ""


class Ledger(BaseModel):
    project_id: str
    entries: list[LedgerEntry] = Field(default_factory=list)
