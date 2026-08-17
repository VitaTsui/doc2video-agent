"""Executor — runs an ExecutionPlan stage by stage.

The Agent never generates frames itself (方案 §5). It maintains the
VideoProject, picks skills, calls tools, checks results, and saves after every
stage so a failed run can resume instead of starting over.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..core.errors import Doc2VideoError, SkillFailed
from ..core.logging import get_logger
from ..schemas import HistoryEntry, ProjectStatus, Scene, VideoProject
from ..skills import (
    DirectorSkill,
    DocumentSkill,
    MotionSkill,
    NarrationSkill,
    ReviewSkill,
    SkillContext,
    VoiceSkill,
)
from ..tools import ffmpeg
from ..tools.parsers import parse as parse_document
from ..tools.renderer import ScenePlan, select_adapter
from .planner import ExecutionPlan, Stage

log = get_logger(__name__)

ProgressFn = Callable[[str, str], None]

STAGE_STATUS = {
    Stage.PARSE: ProjectStatus.PARSING,
    Stage.UNDERSTAND: ProjectStatus.PARSING,
    Stage.NARRATE: ProjectStatus.WRITING,
    Stage.VOICE: ProjectStatus.VOICING,
    Stage.DIRECT: ProjectStatus.DIRECTING,
    Stage.MOTION: ProjectStatus.DIRECTING,
    Stage.RENDER: ProjectStatus.RENDERING,
    Stage.REVIEW: ProjectStatus.REVIEWING,
}


class Executor:
    def __init__(self, ctx: SkillContext, *, progress: ProgressFn | None = None) -> None:
        self.ctx = ctx
        self._progress = progress or (lambda stage, message: None)

    @property
    def project(self) -> VideoProject:
        return self.ctx.project

    # -- entry point -------------------------------------------------------
    def run(self, plan: ExecutionPlan, *, message: str = "") -> VideoProject:
        if plan.intent is not None:
            self.project.intent = plan.intent

        changed_scenes: list[str] = list(plan.scene_ids)
        try:
            for stage in plan.stages:
                self.project.status = STAGE_STATUS.get(stage, self.project.status)
                self._progress(stage.value, f"开始 {stage.value}")
                self._run_stage(stage, plan)
                self.ctx.store.save(self.project)
                self._progress(stage.value, f"完成 {stage.value}")
            self.project.status = ProjectStatus.READY
        except Doc2VideoError:
            self.project.status = ProjectStatus.FAILED
            self.ctx.store.save(self.project)
            raise
        except Exception as exc:
            self.project.status = ProjectStatus.FAILED
            self.project.render.message = str(exc)
            self.ctx.store.save(self.project)
            raise

        self.project.history.append(
            HistoryEntry(
                message=message or plan.summary,
                actions=[s.value for s in plan.stages],
                changed_scenes=changed_scenes,
            )
        )
        self.ctx.store.save(self.project)
        return self.project

    def _run_stage(self, stage: Stage, plan: ExecutionPlan) -> None:
        handlers = {
            Stage.PARSE: self._stage_parse,
            Stage.UNDERSTAND: self._stage_understand,
            Stage.NARRATE: self._stage_narrate,
            Stage.VOICE: self._stage_voice,
            Stage.DIRECT: self._stage_direct,
            Stage.MOTION: self._stage_motion,
            Stage.RENDER: self._stage_render,
            Stage.REVIEW: self._stage_review,
        }
        handlers[stage](plan)

    # -- stages -------------------------------------------------------------
    def _stage_parse(self, plan: ExecutionPlan) -> None:  # noqa: ARG002
        source_path = self.ctx.asset_path(self.project.source.path)
        if source_path is None or not source_path.exists():
            raise SkillFailed(
                "源文件不存在，无法解析", detail={"path": self.project.source.path}
            )
        document = parse_document(
            source_path,
            self.ctx.store.assets_dir(self.project.project_id),
            target_width=self.ctx.settings.video_width,
        )
        # Preserve the title the user's file already implies.
        document.title = document.title or self.project.source.file
        self.project.document = document
        self.project.source.page_count = len(document.pages)

    def _stage_understand(self, plan: ExecutionPlan) -> None:  # noqa: ARG002
        DocumentSkill(self.ctx).run()

    def _stage_narrate(self, plan: ExecutionPlan) -> None:
        skill = NarrationSkill(self.ctx)
        if not plan.scene_ids:
            skill.run()
            return
        # Targeted edit: rewrite only the named scenes.
        for scene_id in plan.scene_ids:
            scene = self.project.scene(scene_id)
            if scene is None:
                continue
            skill.rewrite_scene(
                scene,
                plan.scene_instructions.get(scene_id, ""),
                plan.scene_durations.get(scene_id),
            )

    def _stage_voice(self, plan: ExecutionPlan) -> None:
        VoiceSkill(self.ctx).run(force=plan.force_voice)

    def _stage_direct(self, plan: ExecutionPlan) -> None:  # noqa: ARG002
        DirectorSkill(self.ctx).run()

    def _stage_motion(self, plan: ExecutionPlan) -> None:  # noqa: ARG002
        MotionSkill(self.ctx).run()

    def _stage_review(self, plan: ExecutionPlan) -> None:  # noqa: ARG002
        ReviewSkill(self.ctx).run()

    # -- rendering ----------------------------------------------------------
    def _stage_render(self, plan: ExecutionPlan) -> None:  # noqa: ARG002
        project = self.project
        if not project.scenes:
            raise SkillFailed("没有可渲染的场景")

        adapter = select_adapter(self.ctx.settings)
        previous_renderer = project.render.renderer
        project.render.renderer = adapter.name
        project.render.status = "rendering"

        # Incremental render, but only when the previous clips are comparable:
        # a different renderer would mix encodings that cannot be concatenated.
        reusable = bool(project.render.scene_clips) and previous_renderer == adapter.name
        dirty = project.dirty_scenes() if reusable else list(project.scenes)
        if dirty:
            log.info("需要渲染 %d / %d 个场景", len(dirty), len(project.scenes))
        else:
            log.info("没有变化的场景，直接重新合成")

        motion = MotionSkill(self.ctx)

        clips_dir = self.ctx.store.clips_dir(project.project_id)
        plans = {p.scene_id: p for p in motion.scene_plans(dirty)}

        for scene in dirty:
            scene_plan = plans.get(scene.scene_id)
            if scene_plan is None:
                continue
            self._progress("render", f"渲染场景 {scene.scene_id}")
            clip_path = clips_dir / f"{scene.scene_id}.mp4"
            adapter.render_scene(scene_plan, clip_path)
            project.render.scene_clips[scene.scene_id] = self.ctx.store.relativize(
                project.project_id, clip_path
            )
            project.render.rendered_scenes[scene.scene_id] = scene.content_hash()

        self._assemble(project.scenes)
        project.render.status = "done"

    def _assemble(self, scenes: list[Scene]) -> None:
        """Concatenate clips, build the narration track, and mux the final MP4."""
        project = self.project
        ffmpeg.ensure_available()

        out_dir = self.ctx.store.out_dir(project.project_id)
        clip_paths: list[Path] = []
        audio_paths: list[Path] = []

        for scene in scenes:
            clip_rel = project.render.scene_clips.get(scene.scene_id)
            clip = self.ctx.store.resolve(project.project_id, clip_rel)
            if clip is None or not clip.exists():
                raise SkillFailed(
                    f"场景 {scene.scene_id} 缺少渲染片段", detail={"clip": clip_rel}
                )
            clip_paths.append(clip)

            audio = self.ctx.asset_path(scene.audio.path)
            if audio is not None and audio.exists():
                audio_paths.append(audio)

        self._progress("render", "合成成片")
        video_only = ffmpeg.concat(clip_paths, out_dir / "video.mp4", work_dir=out_dir)

        final = out_dir / "final.mp4"
        if audio_paths:
            track = ffmpeg.concat_audio(audio_paths, out_dir / "narration.m4a", work_dir=out_dir)
            ffmpeg.mux_audio(video_only, track, final)
        else:
            video_only.replace(final)

        project.render.output_path = self.ctx.store.relativize(project.project_id, final)
        project.render.message = ""


def build_scene_plans(ctx: SkillContext, scene_ids: list[str] | None = None) -> list[ScenePlan]:
    """Expose render plans for inspection / debugging without rendering."""
    motion = MotionSkill(ctx)
    motion.run()
    scenes = (
        [s for s in ctx.project.scenes if s.scene_id in set(scene_ids)]
        if scene_ids
        else ctx.project.scenes
    )
    return motion.scene_plans(scenes)
