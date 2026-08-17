"""Motion — flatten the project into an absolute-timed Timeline and scene plans.

This is the boundary between "business intent" and "frames". Above it everything
is scenes and semantics; below it a renderer adapter sees only absolute times
and normalized rectangles.
"""

from __future__ import annotations

from ..core.errors import SkillFailed
from ..schemas import (
    ActionCue,
    ActionType,
    AudioClip,
    Scene,
    SubtitleCue,
    Timeline,
    VideoClip,
    VisualType,
)
from ..tools.renderer import PlanAction, PlanArea, PlanSubtitle, ScenePlan
from .base import Skill
from .layout import avoids_subtitle_band, build_subtitles, to_frame_area


class MotionSkill(Skill):
    name = "presentation-motion"
    description = "把 Scene 展开为绝对时间轴与渲染计划"

    def run(self) -> None:
        self.project.timeline = self.build_timeline()
        self.log.info(
            "时间轴构建完成：%.1f 秒，%d 个画面片段，%d 条字幕，%d 个动作",
            self.project.timeline.duration,
            len(self.project.timeline.video),
            len(self.project.timeline.subtitles),
            len(self.project.timeline.actions),
        )

    # -- timeline --------------------------------------------------------
    def build_timeline(self) -> Timeline:
        settings = self.ctx.settings
        timeline = Timeline(
            fps=settings.video_fps, width=settings.video_width, height=settings.video_height
        )
        cursor = 0.0

        for scene in self.project.scenes:
            duration = max(scene.duration, 0.5)
            start, end = cursor, cursor + duration

            if scene.visual.asset:
                timeline.video.append(
                    VideoClip(
                        scene_id=scene.scene_id,
                        start=round(start, 3),
                        end=round(end, 3),
                        asset=scene.visual.asset,
                        kind=scene.visual.type.value,
                    )
                )
            if scene.audio.path:
                timeline.audio.append(
                    AudioClip(
                        scene_id=scene.scene_id,
                        start=round(start, 3),
                        end=round(end, 3),
                        asset=scene.audio.path,
                    )
                )

            for cue in build_subtitles(scene):
                timeline.subtitles.append(
                    SubtitleCue(
                        start=round(start + cue.start, 3),
                        end=round(start + cue.end, 3),
                        text=cue.text,
                        scene_id=scene.scene_id,
                    )
                )

            timeline.actions.extend(self._scene_action_cues(scene, offset=start, timeline=timeline))
            cursor = end

        timeline.duration = round(cursor, 3)
        return timeline

    def _scene_action_cues(
        self, scene: Scene, *, offset: float, timeline: Timeline
    ) -> list[ActionCue]:
        page = self.project.document.page(scene.source_page) if scene.source_page else None
        cues: list[ActionCue] = []
        for action in scene.actions:
            area = None
            if action.target and page is not None:
                element = page.element(action.target)
                if element is not None:
                    frame_area = to_frame_area(
                        element.bbox.padded(), page, timeline.width, timeline.height
                    )
                    area = (
                        avoids_subtitle_band(frame_area)
                        if action.type is ActionType.HIGHLIGHT
                        else frame_area
                    )
            cues.append(
                ActionCue(
                    start=round(offset + action.at, 3),
                    end=round(offset + action.at + action.duration, 3),
                    type=action.type,
                    scene_id=scene.scene_id,
                    target=action.target,
                    effect=action.effect,
                    area=area,
                    params=action.params,
                )
            )
        return cues

    # -- render plans ----------------------------------------------------
    def scene_plans(self, scenes: list[Scene] | None = None) -> list[ScenePlan]:
        """Build renderer-facing plans, with times relative to each scene."""
        timeline = self.project.timeline or self.build_timeline()
        targets = scenes if scenes is not None else self.project.scenes
        plans: list[ScenePlan] = []

        for scene in targets:
            window = timeline.scene_window(scene.scene_id)
            if window is None:
                continue
            start, _ = window
            image = self.ctx.asset_path(scene.visual.asset)
            if image is None or not image.exists():
                raise SkillFailed(
                    f"场景 {scene.scene_id} 的画面资源缺失",
                    detail={"asset": scene.visual.asset},
                )
            audio = self.ctx.asset_path(scene.audio.path)

            plans.append(
                ScenePlan(
                    scene_id=scene.scene_id,
                    duration=round(max(scene.duration, 0.5), 3),
                    width=timeline.width,
                    height=timeline.height,
                    fps=timeline.fps,
                    image=str(image) if scene.visual.type is VisualType.SLIDE else None,
                    video=str(image) if scene.visual.type is not VisualType.SLIDE else None,
                    audio=str(audio) if audio and audio.exists() else None,
                    actions=[
                        PlanAction(
                            type=cue.type,
                            start=round(cue.start - start, 3),
                            end=round(cue.end - start, 3),
                            effect=cue.effect,
                            target=cue.target,
                            area=PlanArea(**cue.area.model_dump()) if cue.area else None,
                            params=cue.params,
                        )
                        for cue in timeline.actions
                        if cue.scene_id == scene.scene_id and cue.type is not ActionType.TRANSITION
                    ],
                    subtitles=[
                        PlanSubtitle(
                            start=round(cue.start - start, 3),
                            end=round(cue.end - start, 3),
                            text=cue.text,
                        )
                        for cue in timeline.subtitles
                        if cue.scene_id == scene.scene_id
                    ],
                )
            )
        return plans
