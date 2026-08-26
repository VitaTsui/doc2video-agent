"""presentation-review — quality gate before the project is called ready.

Two layers: deterministic checks that catch structural defects (missing assets,
actions pointing at nothing, wildly-off duration), and an optional model review
for the things only reading the script can catch — flat narration that merely
reads the slide, broken transitions, factual drift.
"""

from __future__ import annotations

import math
import re

from pydantic import BaseModel

from ..core import ledger, tuning
from ..schemas import ElementKind, PageType, ReviewFinding
from ..schemas.telemetry import QualityDimension, QualityReport
from ..tools.renderer.base import SUBTITLE_BOTTOM_MARGIN
from ..tools.tts.providers import SilentProvider
from . import render_review, speech_review
from .base import ProgressFn, Skill

# What a scene's recorded engine is when nothing spoke it.
SILENT_PROVIDER = SilentProvider.name

DURATION_TOLERANCE = 0.25
LONG_SCENE_SECONDS = 90.0
SHORT_SCENE_SECONDS = 3.0
# Below this overlap the script has stopped being about the page in front of
# it. The check used to run the other way — anything *above* 0.72 was flagged as
# 「接近照读」 — and that was right while the script's job was to explain what the
# page could not say for itself. It is not the job any more: the script reads
# the deck, in the deck's own words and order, and a high overlap is the thing
# working. What is left worth catching is the opposite failure, the one that
# actually gets complained about: a page of specifics summarised into 「围绕六大
# 方向展开」, which shares almost nothing with the page it is standing in front
# of. Measured on this deck: pages written to the current prompt overlap the
# page 73–85%, and the summary-style ones ran 28–37%.
UNGROUNDED_THRESHOLD = 0.45

# Below this much text a page cannot ground anything: a cover carries a title
# and a date, and the narration that introduces it necessarily says more than
# the page does. Judging those would flag every divider in the deck.
GROUNDABLE_PAGE_CHARS = 40

# 「平台上有三块开放机制。」 and then the page ends. The three are on the slide,
# in front of the viewer, and the narration announced them and walked away —
# measured on one 30-page film, twice, and it is the thing that gets noticed
# because the sentence sets up an expectation the film never pays.
#
# Anaphora is not an announcement: 「这两块都是……」 points back at two things
# already named. Neither is an ordinal — 「第三部分」 is a section number.
_COUNTED = re.compile(
    r"(?<!第)([两二三四五六七八九]|[2-9])\s*(个|块|类|项|条|种|大|步|方面|部分|层)"
)
_ANAPHORA = ("这", "那", "上述", "以上", "其中", "前面")
# Items are separated by whatever the writer used. 「方案分五部分。一是背景及
# 技术牵头方。二是核心市场痛点分析。……」 names all five and was read as naming
# two, because the split knew about commas and not about full stops — and the
# one separator it did find was the 及 inside 「背景及技术牵头方」.
_ITEM_SPLIT = re.compile(r"[，、；：。！？]|以及|和|及")
_COUNT_VALUE = {"两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


# A block of text on the page worth naming. Shorter than this is a label, a
# page number, or a stray bullet character.
NAMED_ITEM_CHARS = 8
# What naming one block costs, as a share of the block's own size. A heading of
# eight characters is one short sentence; a 200-character paragraph is not read
# out but it is not one sentence either. Sub-linear for the same reason the
# per-page share is: `3.5√n` gives 10 characters to a label, 18 to a line, 28
# to a sentence, 50 to a paragraph. Measured across this deck's 185 blocks
# (8–342 characters each): 3304 characters of script, about 12.4 minutes.
ITEM_SHARE = 3.5
# Nothing is worth fewer words than this — below it a mention is not a sentence.
MIN_ITEM_CHARS = 10
# What a page costs before its items: the sentence that says what the page is.
PAGE_OPENING_CHARS = 25
# How much of a page a narration should walk, as a share of what is on it.
#
# Every block on every page was the rule, and it is right for the half of a
# deck that carries three or four things — 「页面内容少的就按文稿来」. It is
# wrong for the other half: this deck's densest page holds 24 blocks, and
# naming all of them is a page nobody can follow.
#
# Not a ceiling either: a 24-block page and an 8-block page are not equally
# worth six sentences. The share grows with the page and grows more slowly
# than it — `2√n` names everything up to four, six of eight, ten of
# twenty-four. Measured across this deck's 30 pages: 185 blocks unbounded
# (15.8 minutes), 130 under this rule (12.1), and the twelve sparse pages are
# untouched because their own count is already below it.
NAMING_SHARE = 2.0


def _pace(settings) -> float:
    """Characters a second, from the engine that will actually speak them."""
    from ..tools.tts import TTSTool

    return TTSTool(settings).chars_per_second or 4.15


def blocks_of(page) -> list:
    """The page's own blocks of text — what a narration can name."""

    return [
        element
        for element in page.elements
        if element.kind is not ElementKind.TITLE
        and len((element.text or "").strip()) >= NAMED_ITEM_CHARS
    ]


def worth_naming(page) -> int:
    """How many of this page's blocks the script should get through.

    A page with three things on it is read; a page with twenty-four is chosen
    from, and a page with twelve sits in between. One number, used by
    everything that has an opinion about length: the per-page writing budget,
    the check that says a page was walked, and the length proposed for a deck
    nobody gave a length for.
    """
    count = len(blocks_of(page))
    return min(count, math.ceil(NAMING_SHARE * math.sqrt(count))) if count else 0


#: Past this, a page holds more than it can say and summarising it is the
#: honest answer; below it, a page that runs long is padded rather than full.
DENSE_ENOUGH_TO_SUMMARISE = 2.0


def density(page, budget: int) -> float:
    """How much of this page's text the script can afford. Above 1 it cannot.

    Density is how much there is to say, not how many boxes it is in. Counting
    boxes made 「技术牵头方」 a sparse page — four blocks, three of them labels —
    and it was read out in full, while the fourth block is a 342-character
    paragraph and the page carries three times what its budget can say.
    """
    written = len(page.raw_text().strip())
    return written / max(budget, 1)


def item_share_chars(text: str) -> int:
    """What naming this one block costs, by its own size."""
    return max(MIN_ITEM_CHARS, math.ceil(ITEM_SHARE * math.sqrt(len(text.strip()))))


def page_share_chars(page) -> int:
    """Roughly what this page costs to say: an opening plus what it names.

    The blocks it will name are not known in advance — the writer chooses them
    — so the average block on this page stands in for them. A page of labels
    costs less than a page of paragraphs even when both name six things, which
    is the point.
    """
    blocks = blocks_of(page)
    if not blocks:
        return PAGE_OPENING_CHARS
    average = sum(item_share_chars(block.text) for block in blocks) / len(blocks)
    return PAGE_OPENING_CHARS + round(average * worth_naming(page))


def tellable_seconds(document, pace: float, silence: float) -> float:
    """How long this deck takes to tell, at one short sentence per named block.

    The floor under any target. A deck this size does not fit in fifteen
    minutes however tightly it is written, and a score that treats that as a
    failure is marking the film down for the length of the document.
    """
    chars = sum(page_share_chars(page) for page in document.pages)
    return chars / max(pace, 0.1) + silence * len(document.pages)


def missed_items(narration: str, page) -> tuple[int, int, list[str]]:
    """`(named, affordable, missed)` — how much of the page the script walked.

    Measured on a 30-page film: sixteen pages named fewer than half of their
    own blocks, and the ones that hurt are the pages that had the words to
    spare — a four-card layout where the script names the first card, and the
    camera then has nothing to point at for the other three, so the film moves
    on while three-quarters of what is on screen goes unsaid.
    """
    from .director import MENTION_THRESHOLD, _mentioned

    items = blocks_of(page)
    if len(items) < 3:
        return (0, 0, [])
    named = [item for item in items if _mentioned(item.text, narration) >= MENTION_THRESHOLD]
    # Never more than the page is worth walking: a dense page is chosen from,
    # not read out, so 「讲到 6 处、漏了 18 处」 is the script doing its job.
    # What the script's own length could have named, at this page's own cost
    # per block — a page of paragraphs affords fewer mentions than a page of
    # labels for the same number of characters.
    per_item = max(
        MIN_ITEM_CHARS, round(sum(item_share_chars(item.text) for item in items) / len(items))
    )
    affordable = max(1, min(worth_naming(page), len(narration) // per_item))
    missed = [item.text.strip()[:24] for item in items if item not in named]
    return (len(named), affordable, missed)


def _dangling_counts(narration: str) -> list[tuple[str, int, int]]:
    """Counts the script announces and then does not name: `(phrase, said, named)`."""
    sentences = [part for part in re.split(r"(?<=[。！？])", narration) if part.strip()]
    found: list[tuple[str, int, int]] = []
    counted_once: set[str] = set()
    for index, sentence in enumerate(sentences):
        match = _COUNTED.search(sentence)
        if match is None:
            continue
        head = sentence[: match.start()]
        if any(head.endswith(word) for word in _ANAPHORA):
            continue
        # The second time a page says 「四类」 it is pointing back at the four it
        # already named: 「……四是基准测试数据集，管质检。四类支撑模型从基础训练
        # 到质检评估的全流程。」 is a page that named every one of them and was
        # read as naming two.
        if match.group(0) in counted_once:
            continue
        counted_once.add(match.group(0))
        said = _COUNT_VALUE.get(match.group(1)) or int(match.group(1))
        span = sentence + "".join(sentences[index + 1 :])
        named = len([piece for piece in _ITEM_SPLIT.split(span) if piece.strip()])
        if named < said:
            found.append((match.group(0), said, named))
    return found

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

    def run(self, *, progress: ProgressFn | None = None) -> None:
        # Between the checks, and only between them: a job notices it has been
        # asked to stop when progress is reported, and 质检 is one stage of
        # nearly two minutes that reported nothing from start to finish. Four
        # checks in a row on this thread are four places it can be stopped.
        step = 0

        def tick(what: str) -> None:
            nonlocal step
            step += 1
            if progress is not None:
                progress("review", what, step, 4)

        tick("结构与讲稿")
        # Each check is its own entry, with what it found. A single 「质检」 line
        # for six different kinds of looking says only that something was
        # checked — not that the captions were measured against the page's
        # geometry, or that the audio was listened to for the pauses.
        with ledger.call("check:结构与讲稿", "页面、时长、讲稿内容"):
            findings = self._structural_checks()
        found = len(findings)
        # What the viewer sees, which the checks above cannot reach: a caption
        # can be perfectly timed, correctly split and sitting on top of the
        # number it is describing. Geometry, not pixels — the renderer's layout
        # is ours, so the answer is arithmetic.
        tick("字幕")
        with ledger.call("check:字幕", "出界、遮挡页面文字"):
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
        tick("配音")
        with ledger.call("check:配音", "语速、停顿、平铺直叙"):
            findings.extend(
                speech_review.check_speech(
                    self.project,
                    self.ctx.asset_path,
                    lead=tuning.value("voice.lead", self.ctx.settings),
                    tail=tuning.value("voice.tail", self.ctx.settings),
                    speed=self.ctx.settings.tts_speech_rate,
                )
            )
        if self.project.render.scene_clips:
            tick("画面")
            with ledger.call("check:画面", "空画面、动作有没有画出来"):
                findings.extend(render_review.check_frames(self.project, self.ctx.asset_path))
                findings.extend(render_review.check_actions(self.project, self.ctx.asset_path))
        self.log.info("质检各项：结构 %d 条，其余 %d 条", found, len(findings) - found)
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
        broken = (
            by_kind.get("missing_visual", 0)
            + by_kind.get("missing_audio", 0)
            + by_kind.get("silent_audio", 0)
            + by_kind.get("dangling_list", 0)
            + by_kind.get("thin_coverage", 0)
            + uncovered
        )
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
                name="grounding",
                # Two ways a script fails the page it is standing in front of:
                # it summarises the page away, or it writes like a machine.
                # Same dimension because the fix is the same one — write it
                # again, against what is actually on the page.
                score=_ratio_score(
                    by_kind.get("ungrounded", 0) + by_kind.get("ai_tic", 0), len(scenes)
                ),
                weight=0.15,
                detail=(
                    f"{by_kind.get('ungrounded', 0)} 个场景脱离了页面内容，"
                    f"{by_kind.get('ai_tic', 0)} 个场景有 AI 腔句式"
                    # What this score did not look at. Without it a deck of
                    # scanned pages reports a clean 100 here, which is true of
                    # the checks that ran and false of the video.
                    + (
                        f"（另有 {ungroundable} 个场景所在的页没有文字可比对，未参与判断）"
                        if (ungroundable := by_kind.get("ungroundable", 0))
                        else ""
                    )
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
        # A target shorter than the deck can be told in is not a defect in the
        # film. Measured on a 30-page deck: 208 blocks of text, about 22 minutes
        # to name them all once — asked for fifteen, the script was compressed
        # by every means that keeps the content (14%, no item lost) and still
        # ran 22. Scoring that against fifteen marks the film down for the
        # length of the document.
        floor = tellable_seconds(
            document,
            _pace(self.ctx.settings),
            tuning.value("voice.lead", self.ctx.settings)
            + tuning.value("voice.tail", self.ctx.settings),
        )
        if target and floor > target * (1 + DURATION_TOLERANCE):
            findings.append(
                ReviewFinding(
                    severity="warning",
                    kind="undertellable",
                    message=(
                        f"这份文档把每处内容各讲一句就要约 {floor / 60:.0f} 分钟，"
                        f"而目标是 {target / 60:.0f} 分钟——讲稿已经压到不丢内容的极限，"
                        "要更短就得明确说哪些页不讲"
                    ),
                )
            )
            target = floor
        if target and abs(total - target) / target > DURATION_TOLERANCE:
            findings.append(
                ReviewFinding(
                    severity="warning",
                    kind="duration",
                    message=(
                        f"实际时长 {total:.0f} 秒与目标 {target:.0f} 秒偏差超过 "
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
            elif scene.audio.provider == SILENT_PROVIDER:
                # The one defect the rest of this file cannot see. The silent
                # provider writes a real file of exactly the right length, so
                # every check here passes and the film scores full marks —
                # while being mute. It exists so that a machine with no voice
                # still produces a correctly timed video instead of failing at
                # page nine, which is right; what was wrong is that nothing
                # said so afterwards.
                findings.append(
                    ReviewFinding(
                        severity="error", kind="silent_audio", scene_id=scene.scene_id,
                        message=(
                            "这一段是静音占位，不是配音——时间轴和字幕都对，但没有声音。"
                            "装一个本地语音（doc2video voices install）或改用 edge 后重跑配音"
                        ),
                    )
                )

            if (flat := _length_spread(scene.narration, page)) is not None:
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
                walked, affordable, missed = missed_items(scene.narration, page)
                if affordable and walked < affordable:
                    findings.append(
                        ReviewFinding(
                            severity="warning", kind="thin_coverage", scene_id=scene.scene_id,
                            message=(
                                f"这一页讲到了 {walked} 处内容，"
                                f"按讲稿长度本可以讲 {affordable} 处；"
                                f"没讲到的有「{'」「'.join(missed[:2])}」"
                            ),
                        )
                    )

            for phrase, _said, named in _dangling_counts(scene.narration):
                findings.append(
                    ReviewFinding(
                        severity="warning", kind="dangling_list", scene_id=scene.scene_id,
                        message=(
                            f"讲稿说了「{phrase}」却只点到 {named} 项，"
                            "剩下的观众在屏幕上看得见、听不到"
                        ),
                    )
                )

            if page is not None and len(page.raw_text().strip()) >= GROUNDABLE_PAGE_CHARS:
                overlap = _overlap_ratio(scene.narration, page.raw_text())
                if overlap < UNGROUNDED_THRESHOLD:
                    findings.append(
                        ReviewFinding(
                            severity="warning", kind="ungrounded", scene_id=scene.scene_id,
                            message=(
                                f"讲稿与页面文字重合度只有 {overlap:.0%}，"
                                "多半是把页面上的具体内容概括掉了，或者讲了页面没有的东西"
                            ),
                        )
                    )
            elif page is not None:
                # Silence here used to read as a pass. A page with no text to
                # compare against cannot clear this check — it can only skip
                # it — and a deck of nothing but pictures skipped every one of
                # them and scored 100 for grounding. Said out loud instead, so
                # the number comes with what it did and did not look at.
                findings.append(
                    ReviewFinding(
                        severity="info", kind="ungroundable", scene_id=scene.scene_id,
                        message="这一页没有足够的文字可比对，讲稿贴不贴页面，这里判断不了",
                    )
                )

            for label, pattern, advice in AI_TICS:
                if (hit := re.search(pattern, scene.narration)) is None:
                    continue
                # The page's own words are not the writer's tic. 「未来不是慢一点，
                # 是直接失去生存席位」 is on the slide, in those words, and the
                # script even attributes it — 「页面最后一句话是……」. Marking it
                # down is marking the script down for doing the one thing this
                # whole dimension asks of it.
                if page is not None and _quotes_page(hit.group(0), page.raw_text()):
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


#: How much of a flagged phrase has to appear in the page for it to be a quote.
#: Not all of it: 「不是慢一点，是」 is the script's compression of the page's
#: 「不是发展慢一点，是」, and both are the page speaking.
QUOTE_OVERLAP = 0.7


def _quotes_page(phrase: str, page_text: str) -> bool:
    """Is this phrase the page talking, rather than the writer?

    Compared as characters in order rather than as a substring, because the
    script drops a word here and there while quoting — 「不是发展慢一点」 becomes
    「不是慢一点」 — and a substring test calls that original writing.
    """
    stripped = re.sub(r"[，。、！？：；「」（）\s]", "", phrase)
    if len(stripped) < 4:
        return False
    haystack = re.sub(r"[，。、！？：；「」（）\s]", "", page_text)
    cursor = 0
    matched = 0
    for char in stripped:
        found = haystack.find(char, cursor)
        if found >= 0:
            matched += 1
            cursor = found + 1
    return matched / len(stripped) >= QUOTE_OVERLAP


def _spread(lengths: list[int]) -> float:
    """Coefficient of variation: how unequal these lengths are. 0 is identical."""
    if not lengths:
        return 0.0
    mean = sum(lengths) / len(lengths)
    if mean <= 0:
        return 0.0
    return (sum((length - mean) ** 2 for length in lengths) / len(lengths)) ** 0.5 / mean


def _length_spread(narration: str, page=None) -> float | None:
    """How varied this page's sentence lengths are, or None when they are fine.

    Judged against the page rather than against a constant. A contents page of
    five eleven-character lines *should* be told in five equal sentences; the
    old fixed threshold called that 「念起来是平的」 and asked for variety the
    page does not have. What is worth reporting is a script flatter than what
    it is describing — a page holding a 56-character paragraph next to a
    13-character label, told as four sentences of the same length, has flattened
    the document, which is the defect every other check here is about.

    Returns the coefficient of variation only when it is too low to pass — a
    number a reader can act on ("0.11" says flatter than "0.28") — and nothing
    at all when the rhythm is already varied.
    """
    lengths = [len(part) for part in re.split(r"[。！？；]", narration) if part.strip()]
    if len(lengths) < MIN_SENTENCES_FOR_RHYTHM:
        return None
    spread = _spread(lengths)
    if spread >= MIN_LENGTH_SPREAD:
        return None
    # A page with nothing to be varied about asks for nothing.
    if page is not None:
        blocks = [len(block.text.strip()) for block in blocks_of(page)]
        if len(blocks) >= 2 and _spread(blocks) < MIN_LENGTH_SPREAD:
            return None
    return spread
