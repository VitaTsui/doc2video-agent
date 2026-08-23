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
            # What the previous batches left behind, read the way a client
            # polling the API would read it.
            seen.append(len(store.load(skill.project.project_id).scenes))
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
    skill.run()

    # Three batches over nine pages, and each one started with the pages the
    # ones before it had already written.
    assert seen == [0, 4, 8]
    assert len(store.load(skill.project.project_id).scenes) == 9


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

    # And with no request behind the number, the script is left alone.
    skill.project.intent.duration_stated = False
    kept = skill._fit_duration(pages, budgets, dict(drafts))
    assert kept[pages[0].index].narration == long_page
