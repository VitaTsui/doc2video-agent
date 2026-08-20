"""The Agent — one entry point for both "make me a video" and "change page 7".

Everything the outside world can ask for goes through :meth:`Doc2VideoAgent.run`,
keyed by ``project_id``. That is what makes conversational editing, versioning
and incremental rendering a single mechanism rather than three (方案 §13).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core import flags, ledger, prefs, telemetry
from ..core.config import Settings, get_settings
from ..core.errors import InvalidRequest
from ..core.ids import new_project_id
from ..core.logging import get_logger
from ..schemas import ProjectStatus, Source, VideoProject
from ..skills.base import SkillContext
from ..storage import ProjectStore
from ..storage.run_log import RunLog
from ..tools.parsers import detect_source_type
from .executor import Executor, ProgressFn
from .loop import Outcome
from .planner import REVISION_STAGES as _REVISION_STAGES
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
        )


class Doc2VideoAgent:
    def __init__(
        self,
        settings: Settings | None = None,
        store: ProjectStore | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.store = store or ProjectStore(self.settings)
        self.planner = Planner()
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
        # The voice chosen in the window, written into the video rather than
        # read at synthesis time. A video should say which voice it was made
        # with — changing the default later must not silently change what an
        # old project would re-render as.
        project.intent.voice = prefs.load(self.settings).voice
        self.store.save(project)
        log.info("创建工程 %s（来源：%s）", project_id, source_file.name)
        return project

    # -- entry points ---------------------------------------------------------
    def prepare(self, source_file: Path, brief: str) -> VideoProject:
        """Parse a deck and stop.

        The first half of a run: everything that can be decided without knowing
        what the video will *say*. It returns the document model so the caller
        can read the deck and write the script — which is the step this service
        deliberately does not perform. A caller with a model of its own asks
        for that separately, with `draft`.
        """
        project = self.create_project(source_file)
        plan = self.planner.prepare_plan(brief, project)
        with (
            telemetry.run(project.project_id, flags=self._flags(project)) as recorder,
            ledger.recording(self._ledger_path(project), recorder.record.run_id),
        ):
            try:
                ctx = SkillContext.build(project, store=self.store, settings=self.settings)
                project = Executor(ctx).run(plan, message=brief)
            except Exception as exc:
                self._persist_run(project, recorder.finish(status="failed", error=str(exc)))
                raise
            self._persist_run(project, recorder.finish(status="succeeded", message=plan.summary))
        return project

    def run(
        self,
        *,
        message: str,
        project_id: str | None = None,
        files: list[Path] | None = None,
        progress: ProgressFn | None = None,
        narrations: dict[int, str] | None = None,
        scene_narrations: dict[str, str] | None = None,
        draft: bool = False,
    ) -> AgentRunResult:
        if project_id:
            project = self.store.load(project_id)
        else:
            if not files:
                raise InvalidRequest("首次生成需要上传 PDF / PPT / PPTX 文件")
            if len(files) > 1:
                log.warning("当前版本仅处理第一个文件：%s", files[0].name)
            project = self.create_project(files[0])

        active = self._flags(project)

        with (
            telemetry.run(project.project_id, flags=active) as recorder,
            ledger.recording(self._ledger_path(project), recorder.record.run_id),
        ):
            try:
                with recorder.stage_scope("plan"):
                    plan = self._plan_for(
                        message,
                        project,
                        narrations=narrations,
                        scene_narrations=scene_narrations,
                        editing=bool(project_id),
                        draft=draft,
                    )
                ctx = SkillContext.build(
                    project, store=self.store, settings=self.settings
                )
                project = Executor(ctx, progress=progress).run(plan, message=message)
            except Exception as exc:
                self._persist_run(project, recorder.finish(status="failed", error=str(exc)))
                raise
            record = recorder.finish(status="succeeded", message=plan.summary)
            record.quality = project.quality
            self._persist_run(project, record)

        return AgentRunResult.from_project(project, plan)

    def _plan_for(
        self,
        message: str,
        project: VideoProject,
        *,
        narrations: dict[int, str] | None,
        scene_narrations: dict[str, str] | None,
        editing: bool,
        draft: bool = False,
    ) -> ExecutionPlan:
        """Which plan this call is asking for.

        The one distinction worth naming: `narrations` is None when no script
        was supplied and `{}` when an empty one was. Both are falsy and they
        mean opposite things — `{}` came from a caller that said "render this",
        and treating it as "no script" sends the call down the edit branch,
        where it matches no edit rule, skips narration and dies at render with
        「没有可渲染的场景」. That is exactly what happened when the desktop
        app's 开始生成 was pressed with nothing typed, a case its own copy
        promises will fall back to placeholder text.
        """
        if draft:
            # Whatever the caller has written stays; the model fills the rest.
            plan = self.planner.draft_plan(narrations)
        elif narrations is not None:
            plan = self.planner.render_plan(narrations)
        elif editing:
            plan = self.planner.edit_plan(message, project)
        else:
            plan = self.planner.initial_plan(message, project)
        if scene_narrations:
            # A revision names its scenes by supplying their text.
            plan.scene_narrations = dict(scene_narrations)
            plan.scene_ids = list(scene_narrations)
            plan.stages = _REVISION_STAGES
        return plan

    def chat(
        self,
        *,
        project_id: str,
        message: str,
        progress: ProgressFn | None = None,
    ) -> Outcome:
        """One turn of conversation, with the model deciding what to do.

        The alternative — and what this replaces — was a regex guessing at the
        message and a fixed pipeline running whatever it guessed. Here the model
        sees the deck, the current script and the last quality report, and picks
        among the operations that already existed. It gains no new powers over
        the machine; it gains the ability to look at the result and decide the
        next thing.
        """
        from ..tools.llm import get_llm
        from .loop import AgentLoop
        from .session import SESSION_FILE, SessionStore

        project = self.store.load(project_id)
        llm = get_llm(self.settings, rollout_key=project_id)
        sessions = SessionStore(self.store.project_dir(project_id) / SESSION_FILE)

        def render_all(narrations: dict[int, str]) -> None:
            self.run(message=message, project_id=project_id, narrations=narrations,
                     progress=progress)

        def render_scene(scene_id: str, narration: str) -> None:
            self.run(message=message, project_id=project_id,
                     scene_narrations={scene_id: narration}, progress=progress)

        loop = AgentLoop(
            project,
            llm,
            sessions,
            render_all=render_all,
            render_scene=render_scene,
            reload=lambda: self.store.load(project_id),
        )
        # Opened here rather than inside `run`, so the decisions land in the
        # account too — they happen between the renders, not during one.
        with ledger.recording(self._ledger_path(project)):
            return loop.run(message)

    def describe(self, project_id: str) -> AgentRunResult:
        """A project's current state in the shape a job reports.

        Used after a chat turn, which may have rendered several times: what
        matters afterwards is where the project ended up, not which of those
        renders was last.
        """
        project = self.store.load(project_id)
        return AgentRunResult(
            project_id=project.project_id,
            status=project.status.value,
            summary="",
            scene_count=len(project.scenes),
            duration=round(project.total_duration(), 1),
            output_path=project.render.output_path,
            review=[f.model_dump(mode="json") for f in project.review],
            quality=project.quality.score if project.quality else None,
        )

    def _ledger_path(self, project: VideoProject) -> Path:
        return self.store.project_dir(project.project_id) / ledger.LEDGER_FILE

    def read_ledger(self, project_id: str) -> list:
        """The account of how this project got made, oldest first."""
        return ledger.read(self.store.project_dir(project_id) / ledger.LEDGER_FILE)

    def _flags(self, project: VideoProject) -> dict[str, bool]:
        return flags.active_flags(project.project_id, self.settings)

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
