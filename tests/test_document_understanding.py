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

    def complete_json(self, prompt: str, **kwargs):  # noqa: ARG002
        first = self.calls * BATCH + 1
        indexes = list(range(first, min(first + BATCH, PAGES + 1)))
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
                    "title": f"第 {i} 页",
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
