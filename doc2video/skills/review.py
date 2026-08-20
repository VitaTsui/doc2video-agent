"""presentation-review — quality gate before the project is called ready.

Two layers: deterministic checks that catch structural defects (missing assets,
actions pointing at nothing, wildly-off duration), and an optional model review
for the things only reading the script can catch — flat narration that merely
reads the slide, broken transitions, factual drift.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from ..schemas import PageType, ReviewFinding
from ..schemas.telemetry import QualityDimension, QualityReport
from ..tools.renderer.base import SUBTITLE_BOTTOM_MARGIN
from . import render_review, speech_review
from .base import Skill

DURATION_TOLERANCE = 0.25
LONG_SCENE_SECONDS = 90.0
SHORT_SCENE_SECONDS = 3.0
# Above this overlap the script is essentially reading the page aloud.
READALOUD_THRESHOLD = 0.72

# A page whose sentences are all about the same length reads as a machine
# reporting, however accurate it is — speech carries emphasis by breaking
# rhythm, and a flat rhythm has nowhere to put it. Measured as the spread of
# sentence lengths relative to their mean, over enough sentences for the
# number to mean anything.
MIN_SENTENCES_FOR_RHYTHM = 4
MIN_LENGTH_SPREAD = 0.28

# The tics that make a Chinese script sound machine-written. Each is a shape
# rather than a topic: a script can be perfectly accurate and still read like a
# summary generator, and the shapes are what gives it away.
#
# Checked here rather than only asked for in the prompt, for the reason review
# exists at all: a model asked not to do something does it less, not never, and
# the only way anyone finds out is by reading 2000 characters looking for it.
# The patterns come from the `chinese-writing-style` rules the prompt states —
# stated there, enforced here.
AI_TICS: tuple[tuple[str, str, str], ...] = (
    (
        "否定断言",
        r"不是[^。，！？]{1,14}[，,]\s*(?:而是|是|只是)|并非[^。，！？]{1,14}[，,]\s*而是"
        r"|与其说是[^。]{1,16}不如说|重点不在[^。，]{1,12}[，,]\s*而在",
        "「不是A而是B」这类对比架子，改成直接说肯定的那半句",
    ),
    (
        "顶针重锤",
        r"是([\u4e00-\u9fa5]{1,3})[，,]\1",
        "「是X，X……」把一个词敲两遍，改成把那件事讲具体",
    ),
    (
        "评价尾巴",
        r"这(?:就)?是[^。！？]{0,14}的(?:关键|核心|价值所在|根本|本钱)|说白了|别人学不来",
        "给页面下断语的尾巴，事实说完就走",
    ),
    (
        "举牌词",
        r"值得一提的是|不难看出|需要强调的是|更重要的是|众所周知",
        "举牌提醒别人注意，改成把那件事讲具体",
    ),
)
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
        # What the viewer sees, which the checks above cannot reach: a caption
        # can be perfectly timed, correctly split and sitting on top of the
        # number it is describing. Geometry, not pixels — the renderer's layout
        # is ours, so the answer is arithmetic.
        findings.extend(
            render_review.check_subtitles(
                self.project,
                self.ctx.settings.video_width,
                self.ctx.settings.video_height,
                SUBTITLE_BOTTOM_MARGIN,
            )
        )
        # And whether anything was actually drawn. Only once there are clips to
        # look at: before a render there is no picture to have an opinion about.
        # And how it sounds, which neither of the others can hear.
        findings.extend(
            speech_review.check_speech(
                self.project,
                self.ctx.asset_path,
                lead=self.ctx.settings.scene_lead_seconds,
                tail=self.ctx.settings.scene_tail_seconds,
            )
        )
        if self.project.render.scene_clips:
            findings.extend(render_review.check_frames(self.project, self.ctx.asset_path))
            findings.extend(render_review.check_actions(self.project, self.ctx.asset_path))
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
        project = self.project
        scenes = project.scenes
        by_kind: dict[str, int] = {}
        for finding in findings:
            by_kind[finding.kind] = by_kind.get(finding.kind, 0) + 1

        # A page with no scene is as incomplete as a scene with no audio, and
        # more damaging: nobody watching can tell that something was left out.
        # Counted from the deck rather than from the findings — those collapse
        # into one readable line, and one line must not read as one page.
        uncovered = len(self._uncovered_pages())
        broken = by_kind.get("missing_visual", 0) + by_kind.get("missing_audio", 0) + uncovered
        # Against what the video should have had, not what it has — otherwise
        # dropping most of the deck improves the denominator.
        expected = len(scenes) + uncovered
        dangling = by_kind.get("dangling_action", 0) + by_kind.get("action_overflow", 0)
        dimensions = [
            QualityDimension(
                name="render",
                # A scene that came out empty is not a lesser video, it is a
                # missing one — and unlike everything else here, nothing in the
                # project can tell you it happened.
                score=_ratio_score(
                    by_kind.get("blank_frame", 0) + by_kind.get("action_not_visible", 0),
                    max(len(scenes), 1),
                ),
                weight=0.20,  # with completeness, half the score is "is there a video"
                detail=(
                    f"{by_kind.get('blank_frame', 0)} 个场景画面是空的，"
                    f"{by_kind.get('action_not_visible', 0)} 个动作没画出来"
                ),
            ),
            QualityDimension(
                name="completeness",
                score=_ratio_score(broken, expected),
                weight=0.30,
                detail=(
                    f"{broken} 处不完整"
                    + (f"，其中 {uncovered} 页没有进片" if uncovered else "")
                ),
            ),
            QualityDimension(
                name="pacing",
                score=self._pacing_score(by_kind),
                weight=0.15,
                detail=(
                    f"时长偏差与节奏问题 {by_kind.get('pacing', 0)} 条，"
                    f"语速 {by_kind.get('speech_rate', 0)} 条，"
                    f"平铺直叙 {by_kind.get('monotone', 0)} 条"
                ),
            ),
            QualityDimension(
                name="originality",
                # Two ways a script fails to be worth listening to: it reads the
                # page aloud, or it writes like a machine. Same dimension
                # because the fix is the same one — write it again, properly.
                score=_ratio_score(
                    by_kind.get("read_aloud", 0) + by_kind.get("ai_tic", 0), len(scenes)
                ),
                weight=0.15,
                detail=(
                    f"{by_kind.get('read_aloud', 0)} 个场景接近照读，"
                    f"{by_kind.get('ai_tic', 0)} 个场景有 AI 腔句式"
                ),
            ),
            QualityDimension(
                name="direction",
                score=self._direction_score(dangling),
                weight=0.10,
                detail=f"{self._scenes_with_actions()}/{len(scenes)} 个场景有镜头动作",
            ),
            QualityDimension(
                name="subtitles",
                # Three ways a caption goes wrong and only one of them is about
                # the text: too long to read, off the frame, or on top of the
                # thing being talked about.
                score=_ratio_score(
                    by_kind.get("subtitle", 0)
                    + by_kind.get("subtitle_overflow", 0)
                    + by_kind.get("subtitle_cover", 0),
                    max(len(project.timeline.subtitles), 1),
                ),
                weight=0.10,
                detail=(
                    f"字幕问题 {by_kind.get('subtitle', 0)} 条，"
                    f"出界 {by_kind.get('subtitle_overflow', 0)} 条，"
                    f"遮挡 {by_kind.get('subtitle_cover', 0)} 条"
                ),
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
        # How it is delivered is part of the pace: a page read at 380
        # characters a minute is a page nobody follows, whatever the script
        # says. Weighed lighter than a structural pacing problem — it is a
        # blemish, not a scene that cannot be watched.
        score -= 6.0 * (by_kind.get("speech_rate", 0) + by_kind.get("monotone", 0))
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
    def _uncovered_pages(self) -> list[int]:
        """Pages the deck has and the video does not.

        Checked against the document rather than against ``presentation_order``,
        because the order is itself something the pipeline computes — asking a
        truncated ordering whether anything is missing gets it to vouch for the
        truncation it caused.
        """
        project = self.project
        covered = {scene.source_page for scene in project.scenes if scene.source_page}
        return [
            page.index
            for page in project.document.pages
            if page.index not in covered and page.page_type is not PageType.CONTACT
        ]

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

        missing = self._uncovered_pages()
        if missing:
            shown = "、".join(str(i) for i in missing[:8])
            findings.append(
                ReviewFinding(
                    severity="error",
                    kind="uncovered_page",
                    message=(
                        f"{len(missing)} 页没有出现在成片里（第 {shown} "
                        f"{'…' if len(missing) > 8 else ''}页）"
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

            if (flat := _length_spread(scene.narration)) is not None:
                findings.append(
                    ReviewFinding(
                        severity="warning", kind="pacing", scene_id=scene.scene_id,
                        message=(
                            f"句子长度过于均匀（离散度 {flat:.2f}），"
                            "念出来是一条直线，长短句交错一下"
                        ),
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

            for label, pattern, advice in AI_TICS:
                if (hit := re.search(pattern, scene.narration)) is None:
                    continue
                findings.append(
                    ReviewFinding(
                        severity="warning", kind="ai_tic", scene_id=scene.scene_id,
                        message=f"{label}：「{hit.group(0)}」——{advice}",
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


def _length_spread(narration: str) -> float | None:
    """How varied this page's sentence lengths are, or None when they are fine.

    Returns the coefficient of variation only when it is too low to pass —
    a number a reader can act on ("0.11" says flatter than "0.28"), and
    nothing at all when the rhythm is already varied.
    """
    lengths = [len(part) for part in re.split(r"[。！？；]", narration) if part.strip()]
    if len(lengths) < MIN_SENTENCES_FOR_RHYTHM:
        return None
    mean = sum(lengths) / len(lengths)
    if mean <= 0:
        return None
    variance = sum((length - mean) ** 2 for length in lengths) / len(lengths)
    spread = variance**0.5 / mean
    return spread if spread < MIN_LENGTH_SPREAD else None
