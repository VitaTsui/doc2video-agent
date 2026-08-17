"""presentation-review — quality gate before the project is called ready.

Two layers: deterministic checks that catch structural defects (missing assets,
actions pointing at nothing, wildly-off duration), and an optional model review
for the things only reading the script can catch — flat narration that merely
reads the slide, broken transitions, factual drift.
"""

from __future__ import annotations

from pydantic import BaseModel

from ..schemas import ReviewFinding
from ..schemas.telemetry import QualityDimension, QualityReport
from ..tools.llm import model_schema
from .base import Skill, load_prompt

DURATION_TOLERANCE = 0.25
LONG_SCENE_SECONDS = 90.0
SHORT_SCENE_SECONDS = 3.0
# Above this overlap the script is essentially reading the page aloud.
READALOUD_THRESHOLD = 0.72
MAX_SUBTITLE_CUE_CHARS = 34


class LLMFinding(BaseModel):
    scene_id: str
    severity: str
    kind: str
    message: str


class ReviewResult(BaseModel):
    findings: list[LLMFinding]


class ReviewSkill(Skill):
    name = "presentation-review"
    description = "检测事实、节奏、字幕、音画同步和视觉质量"

    def run(self) -> None:
        findings = self._structural_checks()
        findings.extend(
            self.try_llm(self._content_review, lambda: [], what="内容质检")
        )
        self.project.review = findings
        self.project.quality = self._score(findings)

        errors = sum(1 for f in findings if f.severity == "error")
        self.log.info(
            "质检完成：%d 条问题（其中 error %d 条），质量分 %.1f",
            len(findings),
            errors,
            self.project.quality.score,
        )

    # -- scoring ---------------------------------------------------------
    def _score(self, findings: list[ReviewFinding]) -> QualityReport:
        """Turn findings and structure into one comparable number.

        Scored per dimension rather than as a flat penalty count, because the
        failures are not interchangeable: a scene with no audio is a broken
        video, while a long subtitle cue is a blemish. Each dimension is a
        ratio of what went wrong to what could have, so the score does not drift
        with deck length.
        """
        scenes = self.project.scenes
        by_kind: dict[str, int] = {}
        for finding in findings:
            by_kind[finding.kind] = by_kind.get(finding.kind, 0) + 1

        broken = by_kind.get("missing_visual", 0) + by_kind.get("missing_audio", 0)
        dangling = by_kind.get("dangling_action", 0) + by_kind.get("action_overflow", 0)
        dimensions = [
            QualityDimension(
                name="completeness",
                score=_ratio_score(broken, len(scenes)),
                weight=0.35,
                detail=f"{broken} 个场景缺画面或缺配音",
            ),
            QualityDimension(
                name="pacing",
                score=self._pacing_score(by_kind),
                weight=0.20,
                detail=f"时长偏差与节奏问题 {by_kind.get('pacing', 0)} 条",
            ),
            QualityDimension(
                name="originality",
                score=_ratio_score(by_kind.get("read_aloud", 0), len(scenes)),
                weight=0.20,
                detail=f"{by_kind.get('read_aloud', 0)} 个场景讲稿接近照读页面",
            ),
            QualityDimension(
                name="direction",
                score=self._direction_score(dangling),
                weight=0.15,
                detail=f"{self._scenes_with_actions()}/{len(scenes)} 个场景有镜头动作",
            ),
            QualityDimension(
                name="subtitles",
                score=100.0 if not by_kind.get("subtitle") else 60.0,
                weight=0.10,
                detail=f"字幕问题 {by_kind.get('subtitle', 0)} 条",
            ),
        ]
        total_weight = sum(d.weight for d in dimensions)
        score = sum(d.score * d.weight for d in dimensions) / total_weight

        return QualityReport(
            score=round(score, 1),
            dimensions=dimensions,
            errors=sum(1 for f in findings if f.severity == "error"),
            warnings=sum(1 for f in findings if f.severity == "warning"),
        )

    def _pacing_score(self, by_kind: dict[str, int]) -> float:
        score = 100.0
        if by_kind.get("duration"):
            # Missing the requested length is the one the user asked for.
            score -= 40.0
        score -= 10.0 * by_kind.get("pacing", 0)
        return max(0.0, score)

    def _direction_score(self, dangling: int) -> float:
        scenes = self.project.scenes
        if not scenes:
            return 0.0
        covered = self._scenes_with_actions() / len(scenes)
        return max(0.0, covered * 100.0 - 15.0 * dangling)

    def _scenes_with_actions(self) -> int:
        return sum(1 for scene in self.project.scenes if scene.actions)

    # -- deterministic ---------------------------------------------------
    def _structural_checks(self) -> list[ReviewFinding]:
        findings: list[ReviewFinding] = []
        project = self.project
        document = project.document

        total = project.total_duration()
        target = project.intent.duration
        if target and abs(total - target) / target > DURATION_TOLERANCE:
            findings.append(
                ReviewFinding(
                    severity="warning",
                    kind="duration",
                    message=(
                        f"实际时长 {total:.0f} 秒与目标 {target} 秒偏差超过 "
                        f"{DURATION_TOLERANCE:.0%}，可要求压缩或展开讲稿"
                    ),
                )
            )

        for scene in project.scenes:
            page = document.page(scene.source_page) if scene.source_page else None

            if not scene.visual.asset or self.ctx.asset_path(scene.visual.asset) is None:
                findings.append(
                    ReviewFinding(
                        severity="error", kind="missing_visual", scene_id=scene.scene_id,
                        message="场景缺少画面资源",
                    )
                )
            elif not self.ctx.asset_path(scene.visual.asset).exists():
                findings.append(
                    ReviewFinding(
                        severity="error", kind="missing_visual", scene_id=scene.scene_id,
                        message=f"画面文件不存在：{scene.visual.asset}",
                    )
                )

            audio_path = self.ctx.asset_path(scene.audio.path)
            if audio_path is None or not audio_path.exists():
                findings.append(
                    ReviewFinding(
                        severity="error", kind="missing_audio", scene_id=scene.scene_id,
                        message="场景缺少配音，会出现无声段",
                    )
                )

            if scene.duration > LONG_SCENE_SECONDS:
                findings.append(
                    ReviewFinding(
                        severity="warning", kind="pacing", scene_id=scene.scene_id,
                        message=f"场景时长 {scene.duration:.0f} 秒偏长，建议拆分或压缩",
                    )
                )
            elif 0 < scene.duration < SHORT_SCENE_SECONDS:
                findings.append(
                    ReviewFinding(
                        severity="warning", kind="pacing", scene_id=scene.scene_id,
                        message=f"场景时长仅 {scene.duration:.1f} 秒，画面会一闪而过",
                    )
                )

            for action in scene.actions:
                if action.target and (page is None or page.element(action.target) is None):
                    findings.append(
                        ReviewFinding(
                            severity="error", kind="dangling_action", scene_id=scene.scene_id,
                            message=f"动作指向不存在的元素：{action.target}",
                        )
                    )
                if action.at + action.duration > scene.duration + 0.05:
                    findings.append(
                        ReviewFinding(
                            severity="warning", kind="action_overflow", scene_id=scene.scene_id,
                            message=f"动作 {action.type} 超出场景时长",
                        )
                    )

            if page is not None:
                overlap = _overlap_ratio(scene.narration, page.raw_text())
                if overlap > READALOUD_THRESHOLD:
                    findings.append(
                        ReviewFinding(
                            severity="warning", kind="read_aloud", scene_id=scene.scene_id,
                            message=f"讲稿与页面文字重合度 {overlap:.0%}，接近照读，缺少解释增量",
                        )
                    )

        for cue in project.timeline.subtitles:
            if len(cue.text) > MAX_SUBTITLE_CUE_CHARS:
                findings.append(
                    ReviewFinding(
                        severity="info", kind="subtitle", scene_id=cue.scene_id,
                        message=f"字幕单条过长（{len(cue.text)} 字），可能换行溢出",
                    )
                )
                break

        return findings

    # -- model-assisted ---------------------------------------------------
    def _content_review(self) -> list[ReviewFinding]:
        raw = self.llm.complete_json(
            self._render_prompt(), schema=model_schema(ReviewResult), system=load_prompt("review")
        )
        result = ReviewResult.model_validate(raw)
        known = {s.scene_id for s in self.project.scenes}
        return [
            ReviewFinding(
                severity=f.severity if f.severity in ("error", "warning", "info") else "warning",
                kind=f.kind or "content",
                scene_id=f.scene_id if f.scene_id in known else None,
                message=f.message,
            )
            for f in result.findings
            if f.message.strip()
        ]

    def _render_prompt(self) -> str:
        intent = self.project.intent
        lines = [
            f"目标时长：{intent.duration} 秒｜实际：{self.project.total_duration():.0f} 秒",
            f"目标观众：{intent.audience}｜风格：{intent.style}",
            "",
        ]
        for scene in self.project.scenes:
            page = self.project.document.page(scene.source_page) if scene.source_page else None
            lines.append(
                f"## {scene.scene_id}（第 {scene.source_page} 页，{scene.duration:.0f} 秒）"
            )
            lines.append(f"讲稿：{scene.narration}")
            if page is not None:
                lines.append(f"页面文字：{page.raw_text()[:400]}")
            if scene.actions:
                lines.append(
                    "动作："
                    + "；".join(
                        f"{a.at:.1f}s {a.type}->{a.target or '整页'}" for a in scene.actions
                    )
                )
            lines.append("")
        return "\n".join(lines)


def _ratio_score(bad: int, total: int) -> float:
    """100 when nothing is wrong, falling to 0 when everything is."""
    if total <= 0:
        return 0.0
    return max(0.0, (1.0 - bad / total) * 100.0)


def _overlap_ratio(narration: str, page_text: str) -> float:
    """Character-bigram overlap — cheap, language-agnostic, good enough as a flag."""
    if not narration or not page_text:
        return 0.0
    narration_grams = {narration[i : i + 2] for i in range(len(narration) - 1)}
    page_grams = {page_text[i : i + 2] for i in range(len(page_text) - 1)}
    if not narration_grams:
        return 0.0
    return len(narration_grams & page_grams) / len(narration_grams)
