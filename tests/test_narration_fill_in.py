"""Half-written scripts: the model fills the gaps and leaves the rest alone.

The common case is not "write me a script" or "here is my script" — it is a
person who typed the three pages they care about and wants the other twenty
filled in. Two things have to hold for that to be usable, and neither is
automatic: what they wrote comes back word for word, and what gets written
around it reads as the same script rather than as a stranger's.
"""

from __future__ import annotations

from doc2video.core.config import Settings
from doc2video.schemas import DocumentPage, Source, SourceType, VideoProject
from doc2video.skills import NarrationSkill
from doc2video.skills.base import SkillContext
from doc2video.storage import ProjectStore

WRITTEN = "这一页我自己写好了，一个字都不要动，就按我写的念。"


class _Recording:
    """Answers every page it is asked for, and keeps the prompts it was given."""

    available = True
    model = "fake"
    source = "fake"

    def __init__(self):
        self.prompts: list[str] = []

    def complete_json(self, prompt: str, **kwargs):  # noqa: ARG002
        self.prompts.append(prompt)
        asked = [
            int(line.split("第 ")[1].split(" 页")[0])
            for line in prompt.splitlines()
            if line.startswith("## 第 ")
        ]
        return {
            "pages": [
                {"index": i, "narration": f"模型写的第 {i} 页。", "segments": []} for i in asked
            ]
        }

    def complete_text(self, prompt: str, **kwargs):  # noqa: ARG002
        return ""

    def supports_images(self) -> bool:
        return False


def _skill(settings: Settings, store: ProjectStore, *, pages: int = 9, duration: int = 300):
    project = VideoProject(
        project_id="proj_fill",
        source=Source(type=SourceType.PPTX, file="demo.pptx", path="source/demo.pptx"),
    )
    project.intent.duration = duration
    project.document.pages = [
        DocumentPage(index=i, title=f"第 {i} 页", width=1920, height=1080)
        for i in range(1, pages + 1)
    ]
    skill = NarrationSkill(SkillContext.build(project, store=store, settings=settings))
    store.save(project)
    return skill


def test_what_the_user_wrote_survives_word_for_word(settings: Settings, store: ProjectStore):
    skill = _skill(settings, store)
    skill.ctx.llm = _Recording()

    skill.run({3: WRITTEN})

    kept = [s for s in skill.project.scenes if s.source_page == 3]
    assert len(kept) == 1
    assert kept[0].narration == WRITTEN
    # And every other page did get written.
    assert all(scene.narration for scene in skill.project.scenes)


def test_the_model_is_not_asked_to_write_pages_that_are_already_written(
    settings: Settings, store: ProjectStore
):
    """A deck where one batch is fully written costs one call less."""
    skill = _skill(settings, store)
    llm = _Recording()
    skill.ctx.llm = llm

    # Pages 1-4 are the first batch; writing all four leaves nothing to ask.
    skill.run({1: WRITTEN, 2: WRITTEN, 3: WRITTEN, 4: WRITTEN})

    assert len(llm.prompts) == 2
    assert "## 第 1 页" not in "".join(llm.prompts)


def test_a_gap_is_written_with_its_neighbours_in_view(settings: Settings, store: ProjectStore):
    """The whole point: page 6 has to join onto pages 5 and 7.

    Both of those are in a different batch from page 6, so neither is in the
    prompt by default — carrying them across the batch edge is what makes the
    two halves read as one script.
    """
    skill = _skill(settings, store)
    llm = _Recording()
    skill.ctx.llm = llm

    skill.run({4: "第四页收尾在这里。", 8: "第八页从这里接上。"})

    gap = next(p for p in llm.prompts if "## 第 6 页" in p)
    assert "第四页收尾在这里。" in gap
    assert "第八页从这里接上。" in gap
    assert "待补写" in gap


def test_nothing_the_user_wrote_is_cut_to_fit_the_target_length(
    settings: Settings, store: ProjectStore
):
    """Trimming is for the model's overrun, not for someone's own words."""
    long_text = "我要讲的东西很多，这一段是我自己写的，不许删。" * 12
    skill = _skill(settings, store, pages=4, duration=30)
    skill.ctx.llm = _Recording()

    skill.run({2: long_text})

    kept = next(s for s in skill.project.scenes if s.source_page == 2)
    assert kept.narration == long_text


def test_a_fully_written_deck_needs_no_model_at_all(settings: Settings, store: ProjectStore):
    skill = _skill(settings, store, pages=3)
    llm = _Recording()
    skill.ctx.llm = llm

    skill.run({1: "第一页。", 2: "第二页。", 3: "第三页。"})

    assert llm.prompts == []
    assert [s.narration for s in skill.project.scenes] == ["第一页。", "第二页。", "第三页。"]
