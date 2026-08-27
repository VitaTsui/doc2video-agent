"""What the model is allowed to change about a deck, and what it is not.

Understanding runs in batches — six pages at a time, because a thirty-page deck
does not fit in one useful prompt. That is fine for the per-page fields: each
batch answers about the pages it read. It is not fine for the deck-level ones,
and one of those is the presentation order.

A batch that has read six pages of thirty will answer "the order is 1 to 6",
because that is all it knows. Taken at face value, `ordered_pages()` obeys and
twenty-four pages leave the pipeline without a word — no error, no degradation,
just a video that ends early. That is what these pin down.
"""

from __future__ import annotations

import re

from doc2video.core.config import Settings
from doc2video.schemas import (
    BBox,
    DocumentModel,
    DocumentPage,
    ElementKind,
    SlideElement,
    Source,
    SourceType,
    VideoProject,
)
from doc2video.skills.base import SkillContext
from doc2video.skills.document import DocumentSkill
from doc2video.tools.llm import MockLLM

PAGES = 30
BATCH = 6


class _PerBatchModel(MockLLM):
    """Answers about the pages it was shown, and orders only those.

    Not a strawman — it is the honest answer to the prompt it receives, and
    exactly what a real model returned for a thirty-page 揭榜方案.
    """

    available = True

    def __init__(self) -> None:
        self.calls = 0

    @staticmethod
    def asked_about(prompt: str) -> list[int]:
        """The page numbers this prompt is asking about, in order."""
        return [int(found) for found in re.findall(r"^## 第 (\d+) 页", prompt, re.M)]

    def complete_json(self, prompt: str, **kwargs):  # noqa: ARG002
        # Which pages this call is about, read off the prompt rather than off a
        # counter: batches are read several at a time, so the third call is not
        # the third batch. A real model answers the prompt in front of it, and
        # so does this.
        indexes = self.asked_about(prompt)
        self.calls += 1
        return {
            "topic": "揭榜方案",
            "summary": "一份方案",
            "key_concepts": [],
            "sections": [],
            "presentation_order": indexes,
            "pages": [
                {
                    "index": i,
                    "page_type": "content",
                    "title": f"模型读过的第 {i} 页",
                    "summary": "",
                    "key_points": [],
                    "elements": [],
                }
                for i in indexes
            ],
        }

    def supports_images(self) -> bool:
        return False


def _project() -> VideoProject:
    return VideoProject(
        project_id="proj_order",
        source=Source(type=SourceType.PDF, file="d.pdf", path="source/d.pdf", page_count=PAGES),
        document=DocumentModel(
            pages=[
                DocumentPage(
                    index=i,
                    title=f"第 {i} 页",
                    elements=[
                        SlideElement(
                            id=f"p{i}_e1",
                            kind=ElementKind.PARAGRAPH,
                            text="正文",
                            bbox=BBox(x=0, y=0, w=100, h=100),
                        )
                    ],
                )
                for i in range(1, PAGES + 1)
            ]
        ),
    )


def _understand(project: VideoProject, llm) -> None:
    ctx = SkillContext.build(project, settings=Settings(llm_provider="mock"), llm=llm)
    DocumentSkill(ctx).run()


def test_a_batch_cannot_drop_the_pages_it_never_saw():
    project = _project()
    model = _PerBatchModel()
    _understand(project, model)

    assert model.calls == 5, "三十页应该分五批读完"
    order = project.document.presentation_order
    assert len(order) == PAGES, f"少了 {PAGES - len(order)} 页，它们会从成片里消失"
    assert order[:BATCH] == list(range(1, BATCH + 1)), "第一批说的顺序仍然算数"
    assert sorted(order) == list(range(1, PAGES + 1))


def test_a_batch_may_still_drop_a_page_it_did_read():
    """The rule is about ignorance, not about overriding judgement.

    A model that read a page and left it out meant to leave it out — a divider
    with nothing on it, say. Only the pages outside its batch are put back.
    """

    class _Skips(_PerBatchModel):
        def complete_json(self, prompt: str, **kwargs):
            answer = super().complete_json(prompt, **kwargs)
            if answer["presentation_order"][:1] == [1]:
                answer["presentation_order"] = [1, 2, 4, 5, 6]  # 第 3 页读过，不要
            return answer

    project = _project()
    _understand(project, _Skips())

    order = project.document.presentation_order
    assert 3 not in order
    assert len(order) == PAGES - 1


def test_one_unreadable_batch_costs_only_its_own_pages():
    """It used to cost every batch after it as well.

    The exception left the loop, so a single page whose title carried a
    quotation mark sent the remaining batches to the heuristics too — pages
    that had nothing wrong with them, for a reason none of them caused.
    """

    class _OneBadBatch(_PerBatchModel):
        def complete_json(self, prompt: str, **kwargs):
            answer = super().complete_json(prompt, **kwargs)
            if answer["presentation_order"][0] == 19:
                raise ValueError("返回的结构化结果不是合法 JSON 对象")
            return answer

    project = _project()
    model = _OneBadBatch()
    _understand(project, model)

    assert model.calls == 5, "坏掉的那一批之后，剩下的批次还要接着读"
    titles = {p.index: p.title for p in project.document.pages}
    # The batch that failed keeps whatever the heuristics gave it...
    assert titles[20] == "第 20 页"
    # ...and every other page still has the model's answer, including the
    # batches that came after the failure.
    assert titles[3] == "模型读过的第 3 页"
    assert titles[26] == "模型读过的第 26 页"
    assert len(project.document.presentation_order) == PAGES


def test_batches_are_read_a_few_at_a_time():
    """A batch sees only its own pages, so reading them in turn bought nothing.

    Measured: five calls at 47 seconds each, four minutes of a forty-minute
    film, and nothing one batch understood ever reached another.
    """
    from doc2video.skills.document import MAX_READERS, reading_workers

    assert reading_workers(5, 0) > 1
    assert reading_workers(5, 0) <= MAX_READERS, "上限是模型后面那道闸，不是机器"
    assert reading_workers(5, 2) == 2
    assert reading_workers(1, 8) == 1


def test_what_a_batch_answers_is_applied_in_page_order():
    """The first batch is the one that answers for the deck.

    It holds the cover, and the ordering it returns is merged rather than
    replaced — a batch that saw six pages of thirty must not be allowed to drop
    the other twenty-four. Reading in parallel must not change which batch that
    is, so the answers are applied in page order however they arrive.
    """
    import inspect

    from doc2video.skills.document import DocumentSkill

    source = inspect.getsource(DocumentSkill._read_batches)
    assert "for start, _batch in batches:" in source, "结果要按页码顺序交回去"
    assert "yield answers[start]" in source


def test_the_long_stages_report_often_enough_to_be_stopped():
    """A run is only asked whether it should stop when it reports progress.

    `progress` is where `JobCancelled` is raised, and until now the only places
    it was called from were the boundaries between stages and, inside a render,
    each scene. 理解结构 is one stage four and a half minutes long and 质检 is one
    of nearly two: pressing 「中止」 inside either did nothing at all until it
    finished on its own, which from the outside is a stop button that does not
    stop.

    Both are read here rather than run, because what matters is that the call
    exists on the thread that collects the work — a raise inside a worker would
    only fail that worker's batch.
    """
    import inspect

    from doc2video.agent.executor import Executor
    from doc2video.skills.document import DocumentSkill
    from doc2video.skills.review import ReviewSkill

    reading = inspect.getsource(DocumentSkill._read_batches)
    assert "tick(" in reading, "理解结构要逐批上报，否则中不了"
    assert reading.rindex("tick(") > reading.index("as_completed"), (
        "并行时也要在收集结果那条线程上报"
    )

    checking = inspect.getsource(ReviewSkill.run)
    assert checking.count("tick(") >= 4, "质检的每一项之间都要有一个能停下来的点"

    driving = inspect.getsource(Executor)
    assert "DocumentSkill(self.ctx).run(progress=self._progress)" in driving
    assert "ReviewSkill(self.ctx).run(progress=self._progress)" in driving


def test_groups_are_matched_never_trusted():
    """A group is kept only where its members exist; the rest is dropped.

    An invented member would aim the camera at nothing; a group of one groups
    nothing; and an element claimed by two groups belongs to the first — one
    thing on the page is one thing.
    """
    from doc2video.schemas import BBox, DocumentPage, ElementKind, PageType, SlideElement
    from doc2video.skills.document import DocumentSkill, GroupRead, PageUnderstanding

    page = DocumentPage(
        index=1, page_type=PageType.CONTENT, width=1920, height=1080,
        elements=[
            SlideElement(id=f"e{i}", kind=ElementKind.PARAGRAPH, text=f"第 {i} 块",
                         bbox=BBox(x=100, y=100 * i, w=400, h=60))
            for i in (1, 2, 3)
        ],
    )
    read = PageUnderstanding(
        index=1, page_type=PageType.CONTENT, title="", summary="", key_points=[],
        elements=[],
        groups=[
            GroupRead(members=["e1", "e2", "ghost"], label="e1"),
            GroupRead(members=["e3"]),                      # 单成员，组不成组
            GroupRead(members=["e2", "e3"], label="nope"),  # e2 已被占，label 不在组里
        ],
    )
    DocumentSkill._apply_understanding(page, read)

    assert [g.members for g in page.groups] == [["e1", "e2"]]
    assert page.groups[0].label == "e1"
