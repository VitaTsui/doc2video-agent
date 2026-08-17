"""Skill base class and shared execution context.

A Skill is a *business* capability (understand a deck, lay out a script, direct
attention). Every skill here is **deterministic**: this service holds no model
and makes no model call. The semantic content — what each page should say — is
written by whoever calls it and handed in through the API (see
``skills/narration.py``); everything downstream of that is arithmetic, timing
and rendering, which is exactly the part a model should not be doing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.config import Settings, get_settings
from ..core.logging import get_logger
from ..schemas import VideoProject
from ..storage import ProjectStore


@dataclass
class SkillContext:
    """Everything a skill needs, assembled once per pipeline run."""

    project: VideoProject
    store: ProjectStore
    settings: Settings

    @classmethod
    def build(
        cls,
        project: VideoProject,
        *,
        store: ProjectStore | None = None,
        settings: Settings | None = None,
    ) -> SkillContext:
        settings = settings or get_settings()
        return cls(
            project=project,
            store=store or ProjectStore(settings),
            settings=settings,
        )

    @property
    def project_dir(self) -> Path:
        return self.store.project_dir(self.project.project_id)

    def asset_path(self, relative: str | None) -> Path | None:
        return self.store.resolve(self.project.project_id, relative)


class Skill:
    """Base skill. Subclasses implement ``run`` and report what they changed."""

    name = "skill"
    description = ""

    def __init__(self, ctx: SkillContext) -> None:
        self.ctx = ctx
        self.log = get_logger(f"skill.{self.name}")

    @property
    def project(self) -> VideoProject:
        return self.ctx.project

    def run(self, **kwargs) -> None:
        raise NotImplementedError
