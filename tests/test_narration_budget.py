"""The writing budget has to add up to the duration that was asked for.

Nobody can fix a script that came out the wrong length: the audio is already
synthesised by then and its duration is authoritative. So the budget published
before anything is written is the only place the target duration is enforced.
"""

from __future__ import annotations

import pytest

from doc2video.core.config import Settings
from doc2video.schemas import DocumentPage, Source, SourceType, VideoProject
from doc2video.skills import NarrationSkill
from doc2video.skills.base import SkillContext
from doc2video.storage import ProjectStore


def _skill(settings: Settings, store: ProjectStore, *, pages: int, duration: float):
    project = VideoProject(
        project_id="proj_budget",
        source=Source(type=SourceType.PPTX, file="demo.pptx", path="source/demo.pptx"),
    )
    project.intent.duration = duration
    # These tests are about what happens to a length someone asked for.
    project.intent.duration_stated = True
    project.document.pages = [
        DocumentPage(index=i, title=f"第 {i} 页", width=1920, height=1080)
        for i in range(1, pages + 1)
    ]
    return NarrationSkill(SkillContext.build(project, store=store, settings=settings))


def test_speech_budget_leaves_room_for_the_pauses(settings: Settings, store: ProjectStore):
    """The silence at each end of a page is on screen but is not script."""
    skill = _skill(settings, store, pages=16, duration=480.0)
    guide = skill.guide()

    silence = settings.scene_lead_seconds + settings.scene_tail_seconds
    spoken = sum(row["target_seconds"] for row in guide)
    on_screen = sum(row["page_seconds"] for row in guide)

    assert spoken == pytest.approx(480.0 - silence * 16, abs=1.0)
    assert on_screen == pytest.approx(480.0, abs=1.0)


def test_page_seconds_is_speech_plus_its_own_silence(settings: Settings, store: ProjectStore):
    silence = settings.scene_lead_seconds + settings.scene_tail_seconds
    for row in _skill(settings, store, pages=6, duration=240.0).guide():
        assert row["page_seconds"] == pytest.approx(row["target_seconds"] + silence, abs=0.11)


def test_chars_are_budgeted_from_speech_not_screen_time(settings: Settings, store: ProjectStore):
    row = _skill(settings, store, pages=8, duration=300.0).guide()[0]
    # The engine's own pace, times the rate it was asked to speak at.
    assert row["target_chars"] == int(row["target_seconds"] * 4.6 * settings.tts_speech_rate)


def test_a_short_target_still_leaves_something_to_say(settings: Settings, store: ProjectStore):
    """Silence for 20 pages exceeds a 25s request; the budget must not go negative."""
    guide = _skill(settings, store, pages=20, duration=25.0).guide()

    assert all(row["target_chars"] > 0 for row in guide)
    assert all(row["target_seconds"] > 0 for row in guide)


def test_each_batch_is_saved_as_it_is_written(settings: Settings, store: ProjectStore):
    """A long deck fills in as it is written, not all at once at the end.

    The script takes one model call per batch and each call is a wait. Holding
    every page back until the last one returns means a window that shows
    nothing for minutes and then everything — and there is no reason for it,
    since a finished batch is finished.

    Counted at each save rather than at each call: batches are written several
    at a time now, so what the *next* call sees on disk says nothing — they all
    start together. What matters is that the file grew as the batches landed.
    """
    skill = _skill(settings, store, pages=9, duration=120.0)
    store.save(skill.project)

    seen: list[int] = []

    class _Batched:
        """Answers one batch at a time, and reports what was on disk before it."""

        available = True
        model = "fake"
        source = "fake"

        def __init__(self):
            self.calls = 0

        def complete_json(self, prompt: str, **kwargs):  # noqa: ARG002
            first = self.calls * 4 + 1
            self.calls += 1
            return {
                "pages": [
                    {"text": "", "index": i, "narration": f"第 {i} 页的讲稿。", "segments": []}
                    for i in range(first, min(first + 4, 10))
                ]
            }

        def complete_text(self, prompt: str, **kwargs):  # noqa: ARG002
            return ""

        def supports_images(self) -> bool:
            return False

    skill.ctx.llm = _Batched()

    # How much was on disk each time it was written.
    saved = store.save

    def watch(project):
        result = saved(project)
        seen.append(len(project.scenes))
        return result

    store.save = watch  # type: ignore[method-assign]
    try:
        skill.run()
    finally:
        store.save = saved  # type: ignore[method-assign]

    assert len(store.load(skill.project.project_id).scenes) == 9
    # It grew: the pages arrived in instalments rather than in one lump at the
    # end. The exact instalments depend on which batch finishes first.
    grew = [count for count in seen if count]
    assert len(set(grew)) > 1, f"页面应该是边写边出现的，实际每次都是 {seen}"
    assert max(seen) == 9


def test_a_script_that_overran_is_cut_back_to_the_length_asked_for(settings, store):
    """"做成八分钟" has to mean eight minutes.

    The budget exists before a word is written and the model overruns it
    anyway — on a real 30-page deck it wrote 38% past, and the video came out
    160 seconds longer than the one that was ordered. Review reported that and
    nothing acted on it.

    Not fixed by speeding the voice up: that turns a long script into a rushed
    one rather than a shorter one, and rushed is the thing people complain
    about.
    """
    from doc2video.skills.narration import PageNarration

    skill = _skill(settings, store, pages=6, duration=120.0)
    pages = skill._pages()
    budgets = skill._allocate_budget(pages)

    # Every page written at three times its budget.
    pace = skill._pace()
    drafts = {
        page.index: PageNarration(
            index=page.index,
            narration="".join(f"这是第{n}句话，长度大致相同。" for n in range(30)),
            segments=[],
        )
        for page in pages
    }
    silence = skill._page_silence() * len(pages)
    before = sum(len(d.narration) for d in drafts.values()) / pace + silence
    assert before > 120.0 * 1.5

    fitted = skill._fit_duration(pages, budgets, drafts)
    after = sum(len(d.narration) for d in fitted.values()) / pace + silence

    assert after < before
    assert after <= 120.0 * 1.25
    # Every page still says something: a page cut to nothing is not shorter,
    # it is missing.
    assert all(d.narration.strip() for d in fitted.values())


def test_a_script_already_the_right_length_is_left_alone(settings, store):
    from doc2video.skills.narration import PageNarration

    skill = _skill(settings, store, pages=4, duration=120.0)
    pages = skill._pages()
    budgets = skill._allocate_budget(pages)
    drafts = {
        page.index: PageNarration(index=page.index, narration="很短的一句。", segments=[])
        for page in pages
    }
    assert skill._fit_duration(pages, budgets, drafts) == drafts


def test_trimming_stops_at_a_sentence_boundary():
    """Cutting mid-sentence leaves the narrator stopping in the middle of a thought."""
    from doc2video.skills.narration import _trim_to

    text = "第一句话在这里。第二句话稍微长一点。第三句话是最后一句。"
    trimmed = _trim_to(text, 20)

    assert trimmed.endswith("。")
    assert text.startswith(trimmed)
    # Never down to nothing, however tight the limit.
    assert _trim_to(text, 2) == "第一句话在这里。"


def test_the_pace_follows_the_engine_that_will_speak_it(settings, store):
    """A page budgeted at 4.6 characters a second and spoken at 4.15 runs long.

    The figure used to be written twice — once in the estimator and once in the
    budget — and they agreed until an engine arrived that spoke at a different
    speed.
    """
    from doc2video.core.config import Settings

    fast = _skill(Settings(**{**settings.model_dump(), "tts_provider": "macos_say"}), store,
                  pages=4, duration=120.0)
    assert fast._pace() > 0
    assert fast._char_budget(10.0) == int(10.0 * fast._pace() * settings.tts_speech_rate)


def test_a_script_that_came_up_short_says_so(settings: Settings, store: ProjectStore):
    """A film a sixth shorter than the one that was ordered is not silent news.

    Trimming can bring an overrun back to length; nothing can fill a shortfall
    without writing more of the script. So the one thing that can be done is
    to say it happened — measured at 15% under on a nine-page deck with the
    prompt this ships with.
    """
    from doc2video.core import telemetry
    from doc2video.skills.narration import PageNarration

    skill = _skill(settings, store, pages=4, duration=240.0)
    pages = skill._pages()
    budgets = skill._allocate_budget(pages)
    thin = {
        page.index: PageNarration(index=page.index, narration="一句话。", segments=[])
        for page in pages
    }

    with telemetry.run("proj_short") as recorder:
        skill._fit_duration(pages, budgets, thin)
        record = recorder.finish(status="succeeded")

    said = [d for d in record.degradations if "短" in d.reason]
    assert said, [d.reason for d in record.degradations]


def test_adopting_a_script_does_not_throw_away_the_one_already_written(
    settings: Settings, store: ProjectStore
):
    """A page the caller did not mention is not a page with nothing on it.

    「开始生成」 sends the boxes; a page whose box was never opened arrives as
    nothing. Treating that as 「没有讲稿」 replaced a finished script with
    placeholder text — measured on a 30-page deck: all thirty pages of model
    writing gone, and the film opened with the heuristic's 「这一期我们来讲……」.
    """
    skill = _skill(settings, store, pages=3, duration=120.0)
    skill.apply({1: "第一页是人写的。", 2: "第二页也是。", 3: "第三页同样。"})
    written = {scene.source_page: scene.narration for scene in skill.project.scenes}

    # A second pass that mentions one page only.
    skill.apply({2: "第二页改了。"})
    now = {scene.source_page: scene.narration for scene in skill.project.scenes}

    assert now[2] == "第二页改了。"
    assert now[1] == written[1]
    assert now[3] == written[3]


def test_shortening_a_page_never_takes_its_last_items_away(
    settings: Settings, store: ProjectStore
):
    """Trimming cuts from the end, and the end of a page is where its list is.

    「平台上有三块开放机制。」 is what a trim looks like from the outside: the
    script named all three, the trimmer removed the naming, and the film
    announced a list and moved on. Length is worth less than that.
    """
    from doc2video.skills.narration import PageNarration

    skill = _skill(settings, store, pages=2, duration=20.0)
    pages = skill._pages()
    budgets = skill._allocate_budget(pages)
    whole = "平台上有三块开放机制：技能广场、MCP 工具库、插件集市。" + "补充说明的一句话。" * 6
    drafts = {
        pages[0].index: PageNarration(index=pages[0].index, narration=whole, segments=[]),
        pages[1].index: PageNarration(index=pages[1].index, narration="第二页。", segments=[]),
    }

    fitted = skill._fit_duration(pages, budgets, drafts)

    assert "技能广场" in fitted[pages[0].index].narration


def test_a_script_is_only_cut_to_a_length_someone_asked_for(
    settings: Settings, store: ProjectStore
):
    """480 seconds is a field default, not a request.

    Measured on a 30-page deck: naming each of its 208 blocks in one short
    sentence takes 17 minutes, so trimming to the default was deciding, on
    nobody's behalf, that half of what is on the slides goes unsaid.
    """
    from doc2video.skills.narration import PageNarration

    skill = _skill(settings, store, pages=2, duration=20.0)
    pages = skill._pages()
    budgets = skill._allocate_budget(pages)
    long_page = "这一句是要被压掉的内容。" * 8
    drafts = {
        page.index: PageNarration(index=page.index, narration=long_page, segments=[])
        for page in pages
    }

    trimmed = skill._fit_duration(pages, budgets, dict(drafts))
    assert len(trimmed[pages[0].index].narration) < len(long_page)

    # With no request behind the number the script is not *cut* — the target
    # was proposed from the deck, and losing a page's last sentences to a
    # number nobody asked for is the one thing this must not do. Without a
    # model to rewrite with, that means leaving it as it is.
    skill.project.intent.duration_stated = False
    kept = skill._fit_duration(pages, budgets, dict(drafts))
    assert kept[pages[0].index].narration == long_page


def test_density_is_measured_in_words_not_in_boxes():
    """Four boxes can be denser than sixteen.

    「技术牵头方」 is three labels and one 342-character paragraph. Counting
    boxes made it a sparse page — 「三五处，全讲」 — so the paragraph was read
    out whole. Against its own budget the page carries three times what it can
    say, which is the number that decides how it is told.
    """
    from doc2video.schemas import BBox, DocumentPage, ElementKind, PageType, SlideElement
    from doc2video.skills.narration import _density_note
    from doc2video.skills.review import density, page_share_chars

    def page(index: int, texts: list[str]) -> DocumentPage:
        return DocumentPage(
            index=index,
            title="页",
            page_type=PageType.CONTENT,
            elements=[
                SlideElement(
                    id=f"e{i}",
                    kind=ElementKind.PARAGRAPH,
                    text=text,
                    bbox=BBox(x=0, y=i * 60, w=800, h=50),
                )
                for i, text in enumerate(texts)
            ],
        )

    few_but_long = page(
        1,
        [
            "企业定位 城市产业链智能创新生态运营商",
            "企业愿景 全国产业链引领者",
            "企业宗旨 服务智能生态",
            "二〇一八年十二月，教育部发文批复同意建设……" * 12,
        ],
    )
    # Eight nameable blocks, none of them long: more boxes, less to say.
    many_but_short = page(2, [f"第{i}项要点，一句话说完" for i in range(8)])

    dense = density(few_but_long, page_share_chars(few_but_long))
    sparse = density(many_but_short, page_share_chars(many_but_short))
    assert dense > 2 > sparse, f"四块的密页 {dense:.1f}，八块的稀疏页 {sparse:.1f}"
    # The property, not the wording: a page carrying more than it can say is
    # told not to read its paragraphs out, and a page that fits is not.
    crowded = _density_note(few_but_long, page_share_chars(few_but_long))
    roomy = _density_note(many_but_short, page_share_chars(many_but_short))
    assert "只取名称、数字和结论" in crowded, crowded
    assert "不要概括" in roomy and "只取" not in roomy, roomy


def test_another_round_is_only_worth_it_when_there_is_a_long_way_to_go():
    """Every round is a model call, and they add up to longer than the render.

    Three rounds over twenty-five over-budget pages is seventy-five calls —
    measured at over fifty minutes for one script. A page already near its
    number is near enough; the last few characters are not worth the minute.
    """
    from doc2video.skills.narration import COMPRESSION_ROUNDS, KEEP_COMPRESSING

    ceiling = 100
    # What a first pass typically lands on: a real cut, still well over.
    assert 170 > ceiling * KEEP_COMPRESSING, "还差得远，值得再压一轮"
    # And what a second lands on: close enough to stop.
    assert 120 <= ceiling * KEEP_COMPRESSING, "已经接近了，不该再花一次调用"
    assert COMPRESSION_ROUNDS >= 2, "一轮到不了预算"


def test_a_page_laid_out_as_a_table_says_so():
    """矩阵页要把两条轴都说出来，不能压成一维。

    「(一)高质量数据集」 is four chains across the top and four kinds of dataset
    down the side, with sixteen cells of bullets between them. The writer is
    handed the page as a flat list in reading order and cannot see that, so it
    wrote the cells out one after another — 「预训练数据集，比如核心专利与科研成
    果、招投标商机…」 — taking two items from one column and two from another and
    reading as one list. The page's second axis is gone, and with it what any of
    those items belongs to.

    Found geometrically, because that is where it is; and only there — a page of
    three cards side by side is not a table, and neither is a page of rows.
    """
    from doc2video.schemas import BBox, DocumentPage, ElementKind, PageType, SlideElement
    from doc2video.skills.narration import _matrix_of

    def at(element_id, x, y, w, h, text):
        return SlideElement(
            id=element_id, kind=ElementKind.PARAGRAPH, text=text,
            bbox=BBox(x=x, y=y, w=w, h=h),
        )

    across = ["创新链", "产业链", "人才链", "资金链"]
    down = ["预训练数据集", "指令微调数据集", "强化学习偏好数据集", "基准测试数据集"]
    elements = [
        at(f"c{i}", 390 + i * 406, 286, 108, 48, name) for i, name in enumerate(across)
    ] + [
        at(f"r{i}", 56, 396 + i * 145, 168, 60, name) for i, name in enumerate(down)
    ] + [
        at(f"cell{i}{j}", 255 + j * 406, 366 + i * 145, 300, 120,
           f"• 这一格里的条目之一 • 还有一条 • 再一条（{i}{j}）")
        for i in range(4) for j in range(4)
    ]
    table = DocumentPage(index=15, title="表", page_type=PageType.CONTENT,
                         width=1920, height=1080, elements=elements)

    found = _matrix_of(table)
    assert found is not None, "认不出这是一张表"
    assert found[0] == across and found[1] == down

    # Three cards side by side, each with a heading and a paragraph: not a table.
    cards = DocumentPage(
        index=8, title="三栏", page_type=PageType.CONTENT, width=1920, height=1080,
        elements=[
            at("h1", 147, 440, 386, 42, "市场：AI重构行业淘汰节奏"),
            at("h2", 723, 440, 386, 42, "经营：AI解决企业生存痛点"),
            at("h3", 1304, 440, 354, 42, "发展：组织AI化构建壁垒"),
            at("b1", 149, 529, 424, 90, "率先完成组织改造的企业，交付速度和获客成本形成优势。"),
            at("b2", 721, 529, 455, 90, "重复性事务吞噬人力，替代标准化工作，压缩固定运营开支。"),
            at("b3", 1306, 529, 424, 90, "统一收纳企业业务经验，避免核心人才流失带走经验。"),
        ],
    )
    assert _matrix_of(cards) is None, "三栏并排不是表"
