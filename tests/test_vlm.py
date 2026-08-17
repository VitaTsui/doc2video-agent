"""Page renders attached to the understanding call.

Text extraction is at its weakest exactly where a page carries its meaning in
the picture — a chart, an architecture diagram, a full-bleed screenshot. These
tests pin down which pages get their render sent along, and that the model is
told which ones they are.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

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
from doc2video.skills.document import MAX_IMAGES_PER_BATCH, DocumentSkill
from doc2video.storage import ProjectStore
from doc2video.tools.llm import LLMTool

# Nothing here decodes the render; only its presence on disk is under test.
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class RecordingLLM(LLMTool):
    """Captures what the skill sent instead of calling anything."""

    available = True

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, prompt: str, **kwargs) -> dict[str, Any]:
        self.calls.append({"prompt": prompt, **kwargs})
        return self.response


def _element(element_id: str, kind: ElementKind, text: str = "") -> SlideElement:
    return SlideElement(
        id=element_id,
        kind=kind,
        text=text,
        bbox=BBox(x=0, y=0, w=100, h=100),
        importance=0.5,
    )


def _understanding(pages: list[DocumentPage]) -> dict[str, Any]:
    return {
        "topic": "主题",
        "summary": "摘要",
        "key_concepts": [],
        "sections": [],
        "presentation_order": [p.index for p in pages],
        "pages": [
            {
                "index": page.index,
                "page_type": "content",
                "title": page.title,
                "summary": "",
                "key_points": [],
                "elements": [],
            }
            for page in pages
        ],
    }


@pytest.fixture
def project_dir(settings: Settings, store: ProjectStore) -> Path:
    directory = store.project_dir("proj_vlm") / "assets"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _run(
    pages: list[DocumentPage], settings: Settings, store: ProjectStore
) -> RecordingLLM:
    project = VideoProject(
        project_id="proj_vlm",
        source=Source(type=SourceType.PPTX, file="d.pptx", path="source/d.pptx"),
        document=DocumentModel(pages=pages),
    )
    llm = RecordingLLM(_understanding(pages))
    ctx = SkillContext.build(project, store=store, settings=settings, llm=llm)
    DocumentSkill(ctx).run()
    return llm


def _page(index: int, *, elements, image: Path | None = None, title: str = "页") -> DocumentPage:
    return DocumentPage(
        index=index,
        title=title,
        elements=elements,
        image_path=f"assets/{image.name}" if image else None,
        width=1280,
        height=720,
    )


def _render(project_dir: Path, name: str) -> Path:
    path = project_dir / name
    path.write_bytes(PNG_BYTES)
    return path


def test_chart_page_is_sent_as_an_image(
    settings: Settings, store: ProjectStore, project_dir: Path
):
    image = _render(project_dir, "p1.png")
    pages = [
        _page(1, elements=[_element("e1", ElementKind.CHART, "季度增长")], image=image),
    ]

    llm = _run(pages, settings, store)

    assert llm.calls, "应当调用了 LLM"
    assert llm.calls[0]["images"] == [image]


def test_text_only_page_sends_no_image(
    settings: Settings, store: ProjectStore, project_dir: Path
):
    image = _render(project_dir, "p1.png")
    pages = [
        _page(
            1,
            elements=[
                _element("e1", ElementKind.PARAGRAPH, "这一页全是文字。" * 20),
                _element("e2", ElementKind.PARAGRAPH, "第二段说明文字，同样很长。" * 10),
            ],
            image=image,
        )
    ]

    llm = _run(pages, settings, store)

    assert llm.calls[0]["images"] == []


def test_prompt_names_the_attached_pages(
    settings: Settings, store: ProjectStore, project_dir: Path
):
    image = _render(project_dir, "p2.png")
    pages = [
        _page(
            1,
            elements=[_element("e1", ElementKind.PARAGRAPH, "纯文字页面的内容。" * 20)],
        ),
        _page(2, elements=[_element("e2", ElementKind.CHART, "图表")], image=image),
    ]

    llm = _run(pages, settings, store)

    prompt = llm.calls[0]["prompt"]
    assert "第 2 页" in prompt.split("随附了这些页面的渲染图")[1]
    assert "以图为准" in prompt


def test_missing_render_is_skipped_rather_than_sent(
    settings: Settings, store: ProjectStore, project_dir: Path
):
    """A page can name a render that was never produced."""
    pages = [
        DocumentPage(
            index=1,
            title="图表页",
            elements=[_element("e1", ElementKind.CHART, "图表")],
            image_path="assets/never-written.png",
        )
    ]

    llm = _run(pages, settings, store)

    assert llm.calls[0]["images"] == []
    assert "随附了这些页面的渲染图" not in llm.calls[0]["prompt"]


def test_image_count_per_call_is_capped(
    settings: Settings, store: ProjectStore, project_dir: Path
):
    """Every page in a batch can be visual; the request must not carry them all."""
    pages = [
        _page(
            index,
            elements=[_element(f"e{index}", ElementKind.CHART, "图表")],
            image=_render(project_dir, f"p{index}.png"),
        )
        for index in range(1, MAX_IMAGES_PER_BATCH + 3)
    ]

    llm = _run(pages, settings, store)

    assert len(llm.calls[0]["images"]) == MAX_IMAGES_PER_BATCH


def test_images_are_ordered_by_page_number(
    settings: Settings, store: ProjectStore, project_dir: Path
):
    """A page reference in the prompt has to line up with the image order."""
    pages = [
        _page(
            index,
            elements=[_element(f"e{index}", ElementKind.CHART, "图表")],
            image=_render(project_dir, f"p{index}.png"),
        )
        for index in (1, 2, 3)
    ]

    llm = _run(pages, settings, store)

    names = [path.name for path in llm.calls[0]["images"]]
    assert names == ["p1.png", "p2.png", "p3.png"]
