"""Skill base class and shared execution context.

A Skill is a *business* capability (understand a deck, write a script, direct
attention). Two of them can use a model; the rest are arithmetic, timing and
rendering, which is exactly the part a model should not be doing.

The model is optional everywhere it appears. Whoever calls this service may
write the script themselves and hand it in through the API — that is still the
primary path, and the one MCP clients use. A configured key adds a second path
for callers who have no model of their own (the desktop app); it never replaces
the first. ``try_llm`` is what keeps the two honest: any failure — no key, a
refusal, a malformed reply, no network — lands on the same deterministic
fallback the service has always had, and says so in the run record.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from ..core import ledger, telemetry
from ..core.config import Settings, get_settings
from ..core.logging import get_logger
from ..schemas import VideoProject
from ..storage import ProjectStore
from ..tools.llm import LLMTool, get_llm

T = TypeVar("T")

# (stage, detail, done, total) — see agent/executor.py. Declared here too so a
# skill that reports per-item progress does not have to import the executor.
ProgressFn = Callable[[str, str, int, int], None]

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(name: str) -> str:
    """A prompt, as it will be sent — someone's edit if there is one.

    Edits live in the storage directory rather than beside the code, so an
    update replaces the build and leaves them alone. That is the whole point:
    a prompt you can change and that the next release quietly overwrites is a
    prompt you cannot change.

    Not cached. It used to be, and the reason was that a prompt is read once
    per process — true when the only way to change one was to ship a new
    build. Now the window can change it, and a cache would mean the change
    takes effect on the next restart, which reads as "it did nothing".
    """
    edited = _prompt_override(name)
    if edited is not None:
        return edited
    return shipped_prompt(name)


def shipped_prompt(name: str) -> str:
    """The text this build was released with, whatever anyone has since done."""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def prompt_override_path(name: str, settings=None):
    from ..core.config import get_settings

    return (settings or get_settings()).storage_dir / "prompts" / f"{name}.md"


def _prompt_override(name: str) -> str | None:
    try:
        return prompt_override_path(name).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


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
            llm=llm or get_llm(settings, rollout_key=project.project_id),
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

    def try_llm(self, fn: Callable[[], T], fallback: Callable[[], T], *, what: str) -> T:
        """Run the model path, falling back to the deterministic one.

        Every reason for not getting a model answer is treated the same way,
        because from the video's point of view they are the same: the fallback
        runs and the run record gains a degradation. That record is the only
        way anyone finds out afterwards that a run succeeded while quietly
        producing something worse.
        """
        if not self.llm.available:
            telemetry.record_degradation(what, "未配置模型")
            return fallback()
        # Named before the call, so a failed one still shows what was tried.
        # `source` already distinguishes "gpt-5 via OpenAI" from "gpt-5 via
        # someone's gateway", which is the difference worth seeing here.
        ledger.used(self.llm.source)
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - any failure degrades, none aborts
            # The useful half of these failures lives in `detail` — the reply
            # that would not parse, the CLI's stderr — and a degradation that
            # records only the summary leaves the next person with
            # "返回的结构化结果不是合法 JSON 对象" and nothing to look at.
            reason = str(exc)
            detail = getattr(exc, "detail", None)
            if detail:
                reason += "｜" + "；".join(f"{k}={str(v)[:200]}" for k, v in detail.items())
            self.log.warning("%s 的模型调用失败，改用启发式规则：%s", what, reason)
            telemetry.record_degradation(what, reason)
            return fallback()
