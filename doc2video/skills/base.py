"""Skill base class and shared execution context.

A Skill is a *business* capability (understand a deck, write narration, direct
attention). It may use any Tool, but must never hardcode a vendor or a video
framework — that separation is what lets the renderer or the model change
without touching narrative logic (方案 §6).

Every skill has two paths: an LLM path and a deterministic heuristic path. The
heuristic path is not a stub — it is what keeps the pipeline runnable, testable
and debuggable without a network call.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..core.config import Settings, get_settings
from ..core.logging import get_logger
from ..schemas import VideoProject
from ..storage import ProjectStore
from ..tools.llm import LLMTool, get_llm

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    """Load a prompt template from ``doc2video/prompts``."""
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"缺少提示词文件：{path}")
    return path.read_text(encoding="utf-8")


@dataclass
class SkillContext:
    """Everything a skill needs, assembled once per pipeline run."""

    project: VideoProject
    store: ProjectStore
    settings: Settings
    llm: LLMTool

    @classmethod
    def build(
        cls,
        project: VideoProject,
        *,
        store: ProjectStore | None = None,
        settings: Settings | None = None,
        llm: LLMTool | None = None,
    ) -> SkillContext:
        settings = settings or get_settings()
        return cls(
            project=project,
            store=store or ProjectStore(settings),
            settings=settings,
            llm=llm or get_llm(settings),
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

    @property
    def llm(self) -> LLMTool:
        return self.ctx.llm

    def run(self, **kwargs) -> None:
        raise NotImplementedError

    # -- helpers -------------------------------------------------------
    def try_llm(self, fn, fallback, *, what: str):
        """Run an LLM-backed step, degrading to the heuristic path on failure.

        A model outage must not fail a whole video job — it should downgrade the
        quality of one step and say so (方案 §20 长任务失败).
        """
        if not self.llm.available:
            self.log.info("%s：LLM 不可用，使用启发式规则", what)
            return fallback()
        try:
            return fn()
        except Exception as exc:  # provider errors, malformed output, timeouts
            self.log.warning("%s：LLM 调用失败（%s），降级为启发式规则", what, exc)
            return fallback()
