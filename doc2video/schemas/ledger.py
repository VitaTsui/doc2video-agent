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
    # The page this came off, where there is one. Together with `scene_id`
    # this is how an output finds the call that made it: a stage lists what it
    # produced, a call says what it was working on, and without a key in
    # common the reader gets every sub-step in one block and every output in
    # another, and has to guess which produced which.
    page: int | None = None


class EntryKind(StrEnum):
    STAGE = "stage"
    # One call inside a stage: a model request, a page rasterised, a scene
    # voiced, a clip rendered. A stage says a video was voiced in 40 seconds;
    # these say which scene took eight of them.
    CALL = "call"
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
    # The skill this step is the work of, by the name it has everywhere else:
    # `presentation-narration`, not 「生成讲稿」. The label says what was
    # attempted, this says what ran, and only one of the two can be looked up
    # in the code.
    skill: str = ""
    # For a call, the stage it happened inside — so a reader can fold the
    # calls back under the step that made them instead of reading one long
    # flat list where a thirty-scene render is thirty peers of 「解析文档」.
    parent: int = 0
    # Which tools actually did the work: the parser, the voice, the renderer,
    # the model. A step named 「配音」 says what was attempted; `piper` or
    # `macos_say` says what it came out of, and those differ in ways someone
    # comparing two runs can hear.
    tools: list[str] = Field(default_factory=list)
    status: str = "ok"  # ok | failed | skipped
    duration_s: float = 0.0
    at: datetime = Field(default_factory=_now)
    artifacts: list[Artifact] = Field(default_factory=list)
    # Which run this belongs to, so a project's history stays separable after
    # several edits — "the first render" and "after I shortened page 3".
    run_id: str = ""
    # For a call: the pages and scenes it was working on, as `page:7` /
    # `scene:scn_x`. Outputs are collected once, at the end of the stage that
    # produced them — this is what puts each one back beside the call that
    # made it.
    covers: list[str] = Field(default_factory=list)


class Ledger(BaseModel):
    project_id: str
    entries: list[LedgerEntry] = Field(default_factory=list)
