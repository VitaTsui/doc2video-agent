"""The quality score — what makes two runs comparable.

The score has to move for the reasons a viewer would care about, and stay put
for the ones they wouldn't. In particular it must not drift with deck length:
a 40-page deck with two broken scenes is in better shape than a 4-page deck
with two, and a score that just counted findings would say the opposite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doc2video.core.config import Settings
from doc2video.schemas import (
    ActionType,
    BBox,
    DirectorAction,
    DocumentModel,
    DocumentPage,
    ElementKind,
    Scene,
    SceneAudio,
    SceneVisual,
    SlideElement,
    Source,
    SourceType,
    VideoIntent,
    VideoProject,
)
from doc2video.skills.base import SkillContext
from doc2video.skills.review import ReviewSkill
from doc2video.storage import ProjectStore

# Long enough for the twenty seconds these scenes claim to run. A short line
# against a long clip is a scene read at 35 characters a minute, which the
# speech review is right to flag — the fixture was the unrealistic part.
# Deliberately uneven in length, so the rhythm check has nothing to say either.
DEFAULT_NARRATION = (
    "这一页讲的是系统架构。"
    "请求先进网关，再交给模型，最后落到检索这一层。"
    "三层各管一件事，出了问题也好定位。"
    "先看整体，再看细节。"
    "下面从最底下那层说起，它决定了上面能做到什么。"
)


def _scene(index: int, *, actions: bool = True, narration: str = DEFAULT_NARRATION) -> Scene:
    return Scene(
        scene_id=f"scene_{index:02d}",
        source_page=index,
        title=f"第 {index} 页",
        narration=narration,
        duration=20.0,
        visual=SceneVisual(asset=f"assets/page_{index:03d}.png"),
        audio=SceneAudio(path=f"audio/scene_{index:02d}.wav", duration=20.0),
        actions=(
            [
                DirectorAction(
                    at=2.0, type=ActionType.HIGHLIGHT, target=f"p{index}_e1", duration=3.0
                )
            ]
            if actions
            else []
        ),
    )


def _page(index: int) -> DocumentPage:
    return DocumentPage(
        index=index,
        title=f"第 {index} 页",
        elements=[
            SlideElement(
                id=f"p{index}_e1",
                kind=ElementKind.PARAGRAPH,
                text="系统架构说明",
                bbox=BBox(x=0, y=0, w=100, h=100),
            )
        ],
    )


def _project(scenes: list[Scene], *, duration: int = 100) -> VideoProject:
    return VideoProject(
        project_id="proj_quality",
        source=Source(type=SourceType.PPTX, file="d.pptx", path="source/d.pptx"),
        intent=VideoIntent(duration=duration),
        document=DocumentModel(pages=[_page(s.source_page) for s in scenes]),
        scenes=scenes,
    )


@pytest.fixture
def score(settings: Settings, store: ProjectStore, tmp_path: Path):
    """Score a project with its assets actually on disk, as a real run would."""

    def _score(project: VideoProject):
        base = store.ensure_layout(project.project_id)
        for scene in project.scenes:
            if scene.visual.asset:
                path = base / scene.visual.asset
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"\x89PNG\r\n\x1a\n")
            if scene.audio.path:
                path = base / scene.audio.path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"RIFF")
        ctx = SkillContext.build(project, store=store, settings=settings)
        ReviewSkill(ctx).run()
        return project.quality

    return _score


def test_a_clean_project_scores_high(score):
    report = score(_project([_scene(i) for i in range(1, 6)]))

    assert report.score > 90
    assert report.errors == 0


def test_a_missing_audio_track_costs_completeness(score):
    scenes = [_scene(i) for i in range(1, 6)]
    scenes[0].audio.path = ""

    report = score(_project(scenes))

    completeness = next(d for d in report.dimensions if d.name == "completeness")
    assert completeness.score < 100
    assert report.errors >= 1


def test_the_score_does_not_drift_with_deck_length(score):
    """Two broken scenes out of forty is not the same as two out of four."""
    small = [_scene(i) for i in range(1, 5)]
    large = [_scene(i) for i in range(1, 41)]
    for scenes in (small, large):
        scenes[0].audio.path = ""
        scenes[1].audio.path = ""

    small_score = score(_project(small, duration=80)).score
    large_score = score(_project(large, duration=800)).score

    assert large_score > small_score


def test_a_script_that_left_the_page_behind_costs_grounding(score):
    """The check runs the other way now.

    A high overlap with the page used to be the defect — 「接近照读」 — back when
    the script's job was to explain what the page could not say for itself. The
    script reads the deck now, so overlap is the thing working, and what is
    worth catching is a page of specifics summarised into a sentence that
    shares almost nothing with it.
    """
    page_text = (
        "系统架构说明：请求先进网关，再交给模型，最后落到检索这一层，"
        "三层各管一件事，出了问题也好定位。"
    )

    def deck(narration: str):
        project = _project([_scene(i, narration=narration) for i in range(1, 6)])
        for page in project.document.pages:
            page.elements[0].text = page_text
        return project

    kept = next(
        d for d in score(deck("请求先进网关，再交给模型，最后落到检索这一层。")).dimensions
        if d.name == "grounding"
    )
    lost = next(
        d for d in score(deck("这一部分讲的东西挺重要的，值得关注。")).dimensions
        if d.name == "grounding"
    )

    assert kept.score == 100
    assert lost.score < 100


def test_a_deck_with_no_camera_work_scores_lower_on_direction(score):
    with_actions = score(_project([_scene(i) for i in range(1, 6)]))
    without = score(_project([_scene(i, actions=False) for i in range(1, 6)]))

    assert without.score < with_actions.score
    direction = next(d for d in without.dimensions if d.name == "direction")
    assert direction.score == 0


def test_missing_the_target_duration_costs_pacing(score):
    scenes = [_scene(i) for i in range(1, 6)]  # 100s of video

    on_target = score(_project(scenes, duration=100))
    way_off = score(_project(scenes, duration=400))

    assert way_off.score < on_target.score


def test_dimensions_carry_their_own_evidence(score):
    report = score(_project([_scene(i) for i in range(1, 6)]))

    assert {d.name for d in report.dimensions} == {
        "completeness",
        "pacing",
        "grounding",
        "direction",
        "subtitles",
        # What the viewer sees, which none of the others can reach: a scene
        # whose frames came out empty is a perfect project and a missing video.
        "render",
    }
    assert all(d.detail for d in report.dimensions)
    assert abs(sum(d.weight for d in report.dimensions) - 1.0) < 1e-9


def test_pages_that_never_made_it_into_the_video_are_an_error(score):
    """Scoring only the scenes that exist vouches for whatever was dropped.

    A 30-page deck came out as a 6-page video and scored 100: every scene it
    had was fine, and nothing asked about the twenty-four it didn't. The person
    watching cannot tell — there is no gap to see, the video simply ends.
    """
    scenes = [_scene(i) for i in range(1, 7)]
    project = _project(scenes)
    project.document.pages = [_page(i) for i in range(1, 31)]

    report = score(project)

    missing = [f for f in project.review if f.kind == "uncovered_page"]
    assert missing, "24 页没进片，质检一句话都没说"
    assert "24 页" in missing[0].message
    assert report.errors >= 1

    # Completeness is what this breaks, and it breaks all the way. The total
    # falls to 72 rather than to nothing because the other four dimensions
    # measure the six scenes that do exist, and those are fine — the number
    # alone is not the whole verdict, which is why the finding exists too.
    completeness = next(d for d in report.dimensions if d.name == "completeness")
    assert completeness.score < 25, completeness  # 24/30 页缺失
    assert report.score < 80, report.score


def test_a_page_the_deck_ends_with_may_be_left_out_without_penalty(score):
    """Contact pages are dropped on purpose — the same rule the ordering uses."""
    from doc2video.schemas import PageType

    scenes = [_scene(i) for i in range(1, 4)]
    project = _project(scenes)
    project.document.pages = [_page(i) for i in range(1, 5)]
    project.document.pages[-1].page_type = PageType.CONTACT

    report = score(project)

    assert not [f for f in project.review if f.kind == "uncovered_page"]
    assert report.score > 90


def test_the_ai_tics_a_script_gets_marked_down_for():
    """"AI 腔" has to be a measurement before it can be a complaint.

    The prompt asks the model not to write these shapes; asking makes it rarer,
    never absent, and the only way anyone finds out is by reading two thousand
    characters looking for them. The patterns are the ones the writing-style
    rules name, so the prompt and the gate say the same thing.
    """
    import re

    from doc2video.skills.review import AI_TICS

    hits = {}
    for label, pattern, _ in AI_TICS:
        for text in (
            "揭榜要的不是方案书，是能落地的创新联合体。",
            "它的起点不是分析，而是把分散的数据变成对象。",
            "十六个以上数据源，按需组合，这是可用性的关键。",
            "是快，快在把链路缩到了一步。",
            "值得一提的是，这套流程已经跑通。",
        ):
            if re.search(pattern, text):
                hits.setdefault(label, []).append(text)

    assert set(hits) == {"否定断言", "评价尾巴", "顶针重锤", "举牌词"}
    assert len(hits["否定断言"]) == 2

    # And ordinary negation is left alone: it is normal Chinese, not a tic.
    for _, pattern, _ in AI_TICS:
        assert not re.search(pattern, "这个指标没达标，资料里也没有这一项。")


def test_a_flat_rhythm_is_reported_and_a_varied_one_is_not():
    """Speech puts emphasis by breaking rhythm; a flat one has nowhere to put it.

    Accuracy is not the problem being measured here — every sentence can be
    right and the page still sound like a machine reporting, because they are
    all the same length.
    """
    from doc2video.skills.review import _length_spread

    # Four sentences, all about eighteen characters — the shape the model
    # falls into when nothing asks it not to.
    flat = (
        "先看最底下的一层，也是最费功夫的一层。"
        "数据沿着四条链分维度铺开，不靠堆量。"
        "每条链上再分四类，各自负责一件事情。"
        "十六个以上的数据来源，按需组合起来。"
    )
    varied = (
        "先看最底下这层。"
        "数据沿四条链铺开，每条链再分四类：预训练打底，微调练出能力，偏好数据做对齐。"
        "十六个来源，按需组合。够用了。"
    )

    assert _length_spread(flat) is not None
    assert _length_spread(varied) is None
    # Too few sentences to say anything about rhythm.
    assert _length_spread("只有一句话。") is None


def test_a_script_that_announces_a_list_and_walks_away_is_incomplete(score):
    """「平台上有三块开放机制。」 and then the page ends.

    The three are on the slide, in front of the viewer. Announcing them and
    not naming them sets up an expectation the film never pays — measured
    twice on one 30-page deck, and it is what gets noticed.
    """
    from doc2video.skills.review import _dangling_counts

    assert _dangling_counts("平台上有三块开放机制。") == [("三块", 3, 1)]
    # Named in the same breath: nothing dangling.
    assert _dangling_counts("平台上有三块开放机制：技能广场、MCP 工具库、插件集市。") == []
    # Pointing back at two things already named is not an announcement.
    assert _dangling_counts("这两块都是与本项目高匹配的资源。") == []
    # 「第三部分」 is a section number, not a count.
    assert _dangling_counts("第三部分，石化商业情报智能体。") == []

    scenes = [_scene(i, narration="平台上有三块开放机制。") for i in range(1, 6)]
    completeness = next(
        d for d in score(_project(scenes)).dimensions if d.name == "completeness"
    )
    assert completeness.score < 100


def test_a_page_walked_only_a_quarter_of_the_way_is_flagged(score):
    """「讲了一半就翻页」, measured rather than felt.

    A four-card layout where the script names the first card: the camera then
    has nothing to point at for the other three, and the film moves on while
    three quarters of what is on screen goes unsaid. Sixteen pages of one
    30-page deck named fewer than half of their own blocks.
    """
    from doc2video.schemas import DocumentPage, SlideElement
    from doc2video.skills.review import missed_items

    page = DocumentPage(
        index=1,
        title="市场研判情报",
        elements=[
            SlideElement(
                id=f"e{i}",
                kind=ElementKind.PARAGRAPH,
                text=text,
                bbox=BBox(x=0, y=0, w=9, h=9),
            )
            for i, text in enumerate(
                [
                    "供应链经营风险可控化",
                    "外贸市场拓展精准赋能",
                    "项目招投标竞争优势打造",
                    "产销策略动态优化调整",
                ],
                start=1,
            )
        ],
    )

    walked, affordable, missed = missed_items(
        "一是供应链经营风险可控化，持续监控原料的供需和价格，支撑采购议价，提前识别风险。", page
    )
    assert walked == 1
    assert affordable > walked  # the script had the words for more
    assert "外贸市场拓展精准赋能" in missed

    # And a script that walks the whole page is not flagged.
    walked, affordable, _ = missed_items(
        "一是供应链经营风险可控化。二是外贸市场拓展精准赋能。"
        "三是项目招投标竞争优势打造。四是产销策略动态优化调整。",
        page,
    )
    assert walked >= affordable
