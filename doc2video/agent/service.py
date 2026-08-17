"""The Agent — one entry point for both "make me a video" and "change page 7".

Everything the outside world can ask for goes through :meth:`Doc2VideoAgent.run`,
keyed by ``project_id``. That is what makes conversational editing, versioning
and incremental rendering a single mechanism rather than three (方案 §13).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core import telemetry
from ..core.config import Settings, get_settings
from ..core.errors import InvalidRequest
from ..core.ids import new_project_id
from ..core.logging import get_logger
from ..schemas import ProjectStatus, Source, VideoProject
from ..skills.base import SkillContext
from ..storage import ProjectStore
from ..storage.run_log import RunLog
from ..tools.llm import LLMTool, get_llm
from ..tools.parsers import detect_source_type
from .executor import Executor, ProgressFn
from .planner import ExecutionPlan, Planner

log = get_logger(__name__)


@dataclass
class AgentRunResult:
    project_id: str
    status: str
    summary: str
    stages: list[str] = field(default_factory=list)
    scene_count: int = 0
    duration: float = 0.0
    output_path: str | None = None
    review: list[dict] = field(default_factory=list)
    quality: float | None = None
    cost_usd: float | None = None

    @classmethod
    def from_project(cls, project: VideoProject, plan: ExecutionPlan) -> AgentRunResult:
        return cls(
            project_id=project.project_id,
            status=project.status.value,
            summary=plan.summary,
            stages=[s.value for s in plan.stages],
            scene_count=len(project.scenes),
            duration=round(project.total_duration(), 1),
            output_path=project.render.output_path,
            review=[f.model_dump() for f in project.review],
            quality=project.quality.score if project.quality else None,
            cost_usd=project.telemetry.cost_usd() if project.telemetry else None,
        )


class Doc2VideoAgent:
    def __init__(
        self,
        settings: Settings | None = None,
        store: ProjectStore | None = None,
        llm: LLMTool | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.store = store or ProjectStore(self.settings)
        self.llm = llm or get_llm(self.settings)
        self.planner = Planner(self.llm)
        self.run_log = RunLog(self.settings)

    # -- project creation ---------------------------------------------------
    def create_project(self, source_file: Path) -> VideoProject:
        source_type = detect_source_type(source_file)
        project_id = new_project_id()
        self.store.ensure_layout(project_id)
        stored_path = self.store.import_source(project_id, source_file)

        project = VideoProject(
            project_id=project_id,
            status=ProjectStatus.CREATED,
            source=Source(type=source_type, file=source_file.name, path=stored_path),
        )
        self.store.save(project)
        log.info("创建工程 %s（来源：%s）", project_id, source_file.name)
        return project

    # -- the single entry point ---------------------------------------------
    def run(
        self,
        *,
        message: str,
        project_id: str | None = None,
        files: list[Path] | None = None,
        progress: ProgressFn | None = None,
    ) -> AgentRunResult:
        if project_id:
            project = self.store.load(project_id)
        else:
            if not files:
                raise InvalidRequest("首次生成需要上传 PDF / PPT / PPTX 文件")
            if len(files) > 1:
                log.warning("当前版本仅处理第一个文件：%s", files[0].name)
            project = self.create_project(files[0])

        # Telemetry opens before planning, not before execution: intent parsing
        # is a model call like any other and its cost belongs to the run.
        with telemetry.run(project.project_id) as recorder:
            try:
                with recorder.stage_scope("plan"):
                    plan = (
                        self.planner.edit_plan(message, project)
                        if project_id
                        else self.planner.initial_plan(message, project)
                    )
                ctx = SkillContext.build(
                    project, store=self.store, settings=self.settings, llm=self.llm
                )
                project = Executor(ctx, progress=progress).run(plan, message=message)
            except Exception as exc:
                self._persist_run(project, recorder.finish(status="failed", error=str(exc)))
                raise
            record = recorder.finish(status="succeeded", message=plan.summary)
            record.quality = project.quality
            self._persist_run(project, record)

        return AgentRunResult.from_project(project, plan)

    def _persist_run(self, project: VideoProject, record) -> None:
        """Keep the latest run on the project, every run in the ledger.

        Best-effort on purpose: a video that rendered must not be reported as
        failed because its telemetry could not be written.
        """
        try:
            project.telemetry = record
            self.store.save(project)
        except Exception as exc:  # noqa: BLE001 - telemetry must never fail a run
            log.warning("保存运行遥测失败：%s", exc)
        self.run_log.append(record)

    # -- read paths ----------------------------------------------------------
    def get_project(self, project_id: str) -> VideoProject:
        return self.store.load(project_id)

    def list_projects(self) -> list[dict]:
        return self.store.list_projects()

    def delete_project(self, project_id: str) -> None:
        self.store.delete(project_id)

    def output_file(self, project_id: str) -> Path | None:
        project = self.store.load(project_id)
        return self.store.resolve(project_id, project.render.output_path)
