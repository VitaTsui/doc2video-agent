"""Executor — runs an ExecutionPlan stage by stage.

The Agent never generates frames itself (方案 §5). It maintains the
VideoProject, picks skills, calls tools, checks results, and saves after every
stage so a failed run can resume instead of starting over.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from ..core import ledger, telemetry
from ..core.errors import Doc2VideoError, SkillFailed
from ..core.logging import get_logger
from ..schemas import HistoryEntry, ProjectStatus, Scene, VideoProject
from ..schemas.ledger import Artifact, ArtifactKind
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

# (stage, detail, done, total). ``total`` is 0 when a stage has no countable
# unit of work; a stage that does report one — voicing and rendering both loop
# over scenes — is the only way a client can draw a bar instead of a spinner
# through the minutes that rendering takes.
ProgressFn = Callable[[str, str, int, int], None]


@contextmanager
def _timed(stage: str):
    """Time a stage when a run is being recorded; a no-op otherwise.

    Kept here rather than inside the telemetry module so the executor reads the
    same with or without observability attached.
    """
    recorder = telemetry.current()
    if recorder is None:
        yield
        return
    with recorder.stage_scope(stage):
        yield


# What each stage is called in the account a person reads. The enum values are
# fine for logs and wrong for a UI: "motion" means nothing to someone watching
# their deck become a video.
STAGE_LABEL = {
    Stage.PARSE: "解析文档",
    Stage.UNDERSTAND: "理解结构",
    Stage.NARRATE: "生成讲稿",
    Stage.VOICE: "配音",
    Stage.DIRECT: "设计镜头",
    Stage.MOTION: "编排时间轴",
    Stage.RENDER: "渲染合成",
    Stage.REVIEW: "质检",
}

# Which skill did the work, for the stages that are one skill's job. Named in
# the account because 「生成讲稿」 says what was attempted and
# `presentation-narration` says what was run — and the second is the name that
# appears in the plan, in the logs, and in this repository's own vocabulary.
STAGE_SKILL = {
    Stage.UNDERSTAND: "presentation-understanding",
    Stage.NARRATE: "presentation-narration",
    Stage.VOICE: "presentation-voice",
    Stage.DIRECT: "presentation-director",
    Stage.MOTION: "presentation-motion",
    Stage.REVIEW: "presentation-review",
}


def stage_label(stage: Stage, plan: ExecutionPlan) -> str:
    """What to call this step, given what it is actually about to do.

    Narration is two different jobs behind one name. Writing a script and
    adopting one the caller already wrote both run through `Stage.NARRATE`,
    and since the script became its own step they now happen in that order on
    every project — so the account showed 「生成讲稿」 twice and read as the
    same work done twice. The second one writes nothing; it takes what is in
    the boxes, splits it into segments and rebuilds the scenes.
    """
    if stage is Stage.NARRATE and plan.adopts_script:
        return "采用讲稿"
    return STAGE_LABEL.get(stage, stage.value)

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


# Which dimension a finding belongs to, so the record can group them the way
# the score does. Anything unmapped lands in 「其他」 rather than disappearing.
_DIMENSION_OF = {
    "blank_frame": "画面",
    "action_not_visible": "画面",
    "missing_visual": "完整度",
    "missing_audio": "完整度",
    "uncovered_page": "完整度",
    "dangling_list": "完整度",
    "thin_coverage": "完整度",
    "pacing": "节奏",
    "speech_rate": "节奏",
    "monotone": "节奏",
    "ungrounded": "贴合文档",
    "ai_tic": "贴合文档",
    "dangling_action": "镜头",
    "action_overflow": "镜头",
    "subtitle_overflow": "字幕",
    "subtitle_cover": "字幕",
    "subtitle": "字幕",
    "duration": "节奏",
}


class Executor:
    def __init__(self, ctx: SkillContext, *, progress: ProgressFn | None = None) -> None:
        self.ctx = ctx
        self._progress = progress or (lambda stage, detail, done, total: None)

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
                self._progress(stage.value, f"开始 {stage.value}", 0, 0)
                label = stage_label(stage, plan)
                recorder = ledger.current()
                if recorder is None:
                    with _timed(stage.value):
                        self._run_stage(stage, plan)
                else:
                    with (
                        recorder.stage(label, skill=STAGE_SKILL.get(stage, "")) as artifacts,
                        _timed(stage.value),
                    ):
                        self._run_stage(stage, plan)
                        artifacts.extend(self._artifacts_of(stage))
                self.ctx.store.save(self.project)
                self._progress(stage.value, f"完成 {stage.value}", 0, 0)
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

    def _artifacts_of(self, stage: Stage) -> list[Artifact]:
        """What this step made, as things a person can open.

        Read off the project after the fact rather than threaded out of each
        skill: the project is where every stage already records what it
        produced, so this stays one place instead of eight.
        """
        project = self.project
        if stage is Stage.PARSE:
            return [
                ledger.file_artifact(
                    f"第 {page.index} 页", page.image_path, ArtifactKind.IMAGE, page=page.index
                )
                for page in project.document.ordered_pages()
                if page.image_path
            ]
        if stage is Stage.UNDERSTAND:
            return [
                ledger.text_artifact(
                    f"第 {page.index} 页｜{page.page_type.value}",
                    page.summary or "（无摘要）",
                    page=page.index,
                )
                for page in project.document.ordered_pages()
            ]
        if stage is Stage.NARRATE:
            return [
                ledger.text_artifact(
                    f"第 {scene.source_page} 页讲稿",
                    scene.narration,
                    scene.scene_id,
                    page=scene.source_page,
                )
                for scene in project.scenes
            ]
        if stage is Stage.VOICE:
            spoken = next((s for s in project.scenes if s.audio.path), None)
            said = (
                [
                    ledger.text_artifact(
                        "用的声音",
                        f"{spoken.audio.provider}｜{spoken.audio.voice or '引擎默认音色'}"
                        # Only when it was asked for. A default of 1.00× would
                        # be a lie: every engine declares its own comfortable
                        # pace and speaks at that unless told otherwise.
                        + (
                            f"｜语速 {project.intent.speech_rate:.2f}×"
                            if project.intent.speech_rate
                            else "｜引擎自己的语速"
                        ),
                    )
                ]
                if spoken
                else []
            )
            # First, and belonging to the step rather than to any one page:
            # it is the one fact about this stage that explains how the whole
            # video sounds, and it was previously only inferable from the tool
            # name on the step's own row.
            return said + [
                ledger.file_artifact(
                    f"第 {scene.source_page} 页配音（{scene.duration:.1f}s）",
                    scene.audio.path,
                    ArtifactKind.AUDIO,
                    scene.scene_id,
                    page=scene.source_page,
                )
                for scene in project.scenes
                if scene.audio.path
            ]
        if stage is Stage.DIRECT:
            return [
                ledger.text_artifact(
                    f"第 {scene.source_page} 页镜头",
                    "；".join(
                        f"{a.type.value} @{a.at:.1f}s" + (f" → {a.target}" if a.target else "")
                        for a in scene.actions
                    )
                    or "（这一页没有镜头动作）",
                    scene.scene_id,
                    page=scene.source_page,
                )
                for scene in project.scenes
            ]
        if stage is Stage.MOTION:
            timeline = project.timeline
            by_scene: dict[str, list[str]] = {}
            for cue in timeline.subtitles:
                by_scene.setdefault(cue.scene_id, []).append(cue.text)
            # The whole film first, then each page's own captions — the thing
            # someone opens when a caption looks wrong is that page's captions,
            # not a count of all of them.
            return [
                ledger.text_artifact(
                    "时间轴",
                    f"{len(timeline.video)} 段画面、{len(timeline.subtitles)} 条字幕、"
                    f"共 {timeline.duration:.1f} 秒",
                )
            ] + [
                ledger.text_artifact(
                    f"第 {scene.source_page} 页字幕（{len(by_scene.get(scene.scene_id, []))} 条）",
                    "\n".join(by_scene.get(scene.scene_id, [])) or "（这一页没有字幕）",
                    scene.scene_id,
                    page=scene.source_page,
                )
                for scene in project.scenes
            ]
        if stage is Stage.RENDER:
            artifacts = [
                ledger.file_artifact(
                    f"第 {scene.source_page} 页片段",
                    clip,
                    ArtifactKind.VIDEO,
                    scene.scene_id,
                    page=scene.source_page,
                )
                for scene in project.scenes
                if (clip := project.render.scene_clips.get(scene.scene_id))
            ]
            if project.render.output_path:
                artifacts.append(
                    ledger.file_artifact("成片", project.render.output_path, ArtifactKind.VIDEO)
                )
            return artifacts
        if stage is Stage.REVIEW:
            quality = project.quality
            # The score, then one entry per dimension carrying its own findings.
            # A single list of everything found is the thing nobody reads: the
            # question is always 「字幕这一项为什么扣分」, and that answer was
            # buried among thirty lines about something else.
            groups: dict[str, list[str]] = {}
            for finding in project.review:
                groups.setdefault(_DIMENSION_OF.get(finding.kind, "其他"), []).append(
                    f"[{finding.severity}] {finding.scene_id or '整体'}：{finding.message}"
                )
            artifacts = [
                ledger.text_artifact(
                    f"质量分 {quality.score}" if quality else "质检",
                    "\n".join(
                        f"{row.name}：{row.score:.1f}（{row.detail}）"
                        for row in (quality.dimensions if quality else [])
                    )
                    or "没有发现问题",
                )
            ]
            artifacts += [
                ledger.text_artifact(f"{name}｜{len(lines)} 条", "\n".join(lines))
                for name, lines in sorted(groups.items())
            ]
            return artifacts
        return []

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
            settings=self.ctx.settings,
        )
        # Preserve the title the user's file already implies.
        document.title = document.title or self.project.source.file
        self.project.document = document
        self.project.source.page_count = len(document.pages)

    def _stage_understand(self, plan: ExecutionPlan) -> None:  # noqa: ARG002
        DocumentSkill(self.ctx).run()

    def _stage_narrate(self, plan: ExecutionPlan) -> None:
        """Adopt the caller's script, or fall back to a placeholder.

        The script is content, and content is the caller's job — the plan
        carries whatever it supplied.
        """
        skill = NarrationSkill(self.ctx)
        if plan.scene_ids:
            # Targeted edit: replace only the named scenes. New text wins; an
            # instruction ("压到 20 秒") needs a model to become text.
            for scene_id in plan.scene_ids:
                scene = self.project.scene(scene_id)
                if scene is None:
                    continue
                text = plan.scene_narrations.get(scene_id)
                if text:
                    skill.rewrite_scene(scene, text)
                elif instruction := plan.scene_instructions.get(scene_id):
                    skill.revise_scene(
                        scene, instruction, plan.scene_durations.get(scene_id, 0.0)
                    )
            return
        if plan.adopts_script:
            # Rendering someone's finished script: take it as it is.
            skill.apply(plan.narrations)
        else:
            # Drafting. Whatever came with the plan is what the user already
            # typed — kept as written, and used as context for the rest.
            skill.run(plan.narrations, progress=self._progress)

    def _stage_voice(self, plan: ExecutionPlan) -> None:
        VoiceSkill(self.ctx).run(force=plan.force_voice, progress=self._progress)

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

        adapter = select_adapter(self.ctx.settings, rollout_key=project.project_id)
        previous_renderer = project.render.renderer
        project.render.renderer = adapter.name
        project.render.status = "rendering"

        # Plans for every scene, not just the ones about to be rendered: the
        # plan is what decides whether a scene changed, so it has to exist
        # before that question can be asked. Building one is cheap — no frames.
        plans = {p.scene_id: p for p in MotionSkill(self.ctx).scene_plans()}

        # Incremental render, but only when the previous clips are comparable:
        # a different renderer would mix encodings that cannot be concatenated.
        reusable = bool(project.render.scene_clips) and previous_renderer == adapter.name
        dirty = [
            scene
            for scene in project.scenes
            if not reusable
            or scene.scene_id not in plans
            or project.render.rendered_scenes.get(scene.scene_id)
            != plans[scene.scene_id].fingerprint()
        ]
        if dirty:
            log.info("需要渲染 %d / %d 个场景", len(dirty), len(project.scenes))
        else:
            log.info("没有变化的场景，直接重新合成")

        clips_dir = self.ctx.store.clips_dir(project.project_id)

        for done, scene in enumerate(dirty):
            scene_plan = plans.get(scene.scene_id)
            if scene_plan is None:
                continue
            self._progress("render", f"渲染场景 {scene.scene_id}", done, len(dirty))
            clip_path = clips_dir / f"{scene.scene_id}.mp4"
            adapter.render_scene(scene_plan, clip_path)
            project.render.scene_clips[scene.scene_id] = self.ctx.store.relativize(
                project.project_id, clip_path
            )
            project.render.rendered_scenes[scene.scene_id] = scene_plan.fingerprint()

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

        self._progress("render", "合成成片", len(scenes), len(scenes))
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
