"""PDF parsing: font size is the only structural signal a flat PDF gives us."""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from doc2video.core.errors import UnsupportedSource
from doc2video.schemas import ElementKind
from doc2video.tools.parsers import detect_source_type, parse


@pytest.fixture
def demo_pdf(tmp_path: Path) -> Path:
    doc = fitz.open()
    for index, (title, body) in enumerate(
        [("Doc2Video Agent", "Turn a deck into an explainer video"), ("Metrics", "MAU 12000")],
        start=1,
    ):
        page = doc.new_page(width=960, height=540)
        page.insert_text((60, 100), title, fontsize=36)
        page.insert_text((60, 200), body, fontsize=16)
        page.insert_text((60, 260), f"page {index}", fontsize=10)
    path = tmp_path / "demo.pdf"
    doc.save(path)
    doc.close()
    return path


def test_detect_source_type():
    assert detect_source_type(Path("a.pdf")).value == "pdf"
    assert detect_source_type(Path("a.PPTX")).value == "pptx"
    with pytest.raises(UnsupportedSource):
        detect_source_type(Path("a.key"))


def test_parse_pdf_renders_pages_and_extracts_elements(demo_pdf: Path, tmp_path: Path):
    assets = tmp_path / "assets"
    document = parse(demo_pdf, assets, target_width=1280)

    assert len(document.pages) == 2
    for page in document.pages:
        assert page.image_path
        assert (assets / Path(page.image_path).name).exists()
        # Rendered at the requested width, so bboxes are in output pixels.
        assert page.width == 1280
        assert page.elements


def test_largest_text_becomes_the_title(demo_pdf: Path, tmp_path: Path):
    document = parse(demo_pdf, tmp_path / "assets", target_width=1280)
    first = document.pages[0]

    titles = [el for el in first.elements if el.kind is ElementKind.TITLE]
    assert titles
    assert "Doc2Video" in titles[0].text
    assert first.title.startswith("Doc2Video")


def test_element_boxes_stay_inside_the_page(demo_pdf: Path, tmp_path: Path):
    document = parse(demo_pdf, tmp_path / "assets", target_width=1280)
    for page in document.pages:
        for element in page.elements:
            assert 0 <= element.bbox.x <= page.width
            assert 0 <= element.bbox.y <= page.height
            assert element.bbox.w > 0 and element.bbox.h > 0
