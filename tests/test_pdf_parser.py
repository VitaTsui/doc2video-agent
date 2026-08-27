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


def test_a_paragraph_split_into_lines_is_put_back_together():
    """Some PDFs emit every visual line as its own block.

    Taken at face value each line becomes an element, and two things break at
    once: the text is cut mid-sentence, and a highlight aimed at the sentence
    draws its box around one row of it — which is what the viewer sees as the
    box sitting on the wrong line.
    """
    from doc2video.tools.parsers.pdf_parser import _joined_paragraphs

    def line(text: str, size: float = 12.0) -> dict:
        return {"spans": [{"text": text, "size": size}]}

    blocks = [
        {"type": 0, "bbox": (60, 100, 560, 116), "lines": [line("围绕重点原料建立监测清单，")]},
        {"type": 0, "bbox": (60, 118, 560, 134), "lines": [line("组织为可连续分析的数据。")]},
        # A different column: same size, but it starts somewhere else.
        {"type": 0, "bbox": (700, 100, 900, 116), "lines": [line("右栏另起一段")]},
    ]
    joined = _joined_paragraphs(blocks)

    assert len(joined) == 2
    assert joined[0]["bbox"] == (60, 100, 560, 134)
    assert len(joined[0]["lines"]) == 2
    assert joined[1]["bbox"][0] == 700


def test_a_heading_is_not_swallowed_by_the_paragraph_above_it():
    """A wrong merge aims the camera at two things and lands on neither."""
    from doc2video.tools.parsers.pdf_parser import _joined_paragraphs

    def line(text: str, size: float) -> dict:
        return {"spans": [{"text": text, "size": size}]}

    blocks = [
        {"type": 0, "bbox": (60, 100, 560, 116), "lines": [line("正文一行", 12.0)]},
        # Same left edge, one line below — but twice the type size.
        {"type": 0, "bbox": (60, 120, 560, 148), "lines": [line("下一个标题", 24.0)]},
    ]
    assert len(_joined_paragraphs(blocks)) == 2

    far = [
        {"type": 0, "bbox": (60, 100, 560, 116), "lines": [line("正文一行", 12.0)]},
        # Same size and left edge, but a whole blank line away.
        {"type": 0, "bbox": (60, 160, 560, 176), "lines": [line("隔了一段", 12.0)]},
    ]
    assert len(_joined_paragraphs(far)) == 2


def test_two_columns_do_not_come_back_as_one_element(tmp_path: Path):
    """PyMuPDF groups by proximity, and a four-card layout defeats it.

    The two card numbers sit at the same height, so 「01」 and 「02」 come back as
    one block 970 points wide on a 1920-point page. Nothing downstream can tell
    that from a genuinely wide heading: a highlight on the left card grew a box
    that reached across the page and framed the right card's number too, which
    is what the viewer sees as 「框选框到隔壁去了」.
    """
    doc = fitz.open()
    page = doc.new_page(width=960, height=540)
    page.insert_text((60, 100), "01", fontsize=28)
    page.insert_text((520, 100), "02", fontsize=28)
    page.insert_text((60, 160), "供应链经营风险可控化", fontsize=18)
    path = tmp_path / "columns.pdf"
    doc.save(path)
    doc.close()

    parsed = parse(path, tmp_path / "assets")
    texts = {el.text: el for el in parsed.pages[0].elements if el.text}

    assert "01" in texts and "02" in texts, texts.keys()
    assert not any("01" in text and "02" in text for text in texts), texts.keys()
    # And each keeps its own column: neither box reaches the other's.
    assert texts["01"].bbox.x + texts["01"].bbox.w < texts["02"].bbox.x


def test_a_centred_paragraph_is_put_back_together_too():
    """Centred lines never line up on the left, by definition.

    Their widths differ, so each line starts half the difference further in —
    and the merge test only knew left edges, so a centred caption stayed in
    pieces. Two real cases on one deck: 「浙江大学作为申报单位的」/「国家重点研发
    计划项目」, left edges 5pt apart, and a two-line closing sentence 64pt
    apart. Both had their centres identical to the pixel.

    The vertical test kept them apart as well. It demanded a non-negative gap,
    and a block's box spans the font's whole ascent and descent while the line
    spacing is set tighter than that, so consecutive lines of one paragraph
    overlap by a point or two as a matter of course — measured at -1.0pt and
    -1.8pt.
    """
    from doc2video.tools.parsers.pdf_parser import _joined_paragraphs

    def line(text: str) -> dict:
        return {"spans": [{"text": text, "size": 12.0}]}

    blocks = [
        # Centres both at 252.5; left edges 5 apart; the second starts 1pt
        # above where the first one's box ends.
        {"type": 0, "bbox": (197, 452, 308, 465), "lines": [line("浙江大学作为申报单位的")]},
        {"type": 0, "bbox": (202, 464, 303, 477), "lines": [line("国家重点研发计划项目")]},
    ]
    joined = _joined_paragraphs(blocks)

    assert len(joined) == 1
    assert len(joined[0]["lines"]) == 2


def test_two_columns_still_do_not_merge_when_their_centres_differ():
    """The overlap allowance must not open the door to a side-by-side pair."""
    from doc2video.tools.parsers.pdf_parser import _joined_paragraphs

    def line(text: str) -> dict:
        return {"spans": [{"text": text, "size": 12.0}]}

    blocks = [
        {"type": 0, "bbox": (60, 100, 400, 116), "lines": [line("左栏")]},
        # Same row, so it overlaps the one above by a whole line's height.
        {"type": 0, "bbox": (700, 100, 1040, 116), "lines": [line("右栏")]},
    ]
    assert len(_joined_paragraphs(blocks)) == 2


def test_a_wrapped_chinese_line_joins_the_next_with_nothing_between():
    """A space between lines is an English word break, not a Chinese one.

    「…同行产能规划、技」 and 「术路线，支撑企业…」 came back as 「技 术路线」. That
    reaches the script, and then the engine, which reads the gap as a pause
    inside a word — the same damage `phrasing.py` pays an extra synthesis call
    to repair. 79 elements on one 30-page deck carried it.

    A line the author ended early keeps its space: a label above its value is
    two things, and 「企业定位」 glued to 「城市产业链智能创新生态运营商」 is one
    unsayable one. Which is which is what the line widths say — measured over
    the deck, wrapped lines stop 0.00–1.01 characters short of the column and
    authored breaks stop 10.67 and 26.13 short.
    """
    from doc2video.tools.parsers.pdf_parser import _block_text

    def line(text: str, x1: float) -> dict:
        return {"bbox": (60, 100, x1, 116), "spans": [{"text": text, "size": 10.0}]}

    wrapped = {
        "type": 0,
        "bbox": (60, 100, 560, 134),
        "lines": [line("摸清同行产能规划、技", 550), line("术路线，支撑企业。", 400)],
    }
    assert _block_text(wrapped)[0] == "摸清同行产能规划、技术路线，支撑企业。"

    authored = {
        "type": 0,
        "bbox": (60, 100, 560, 134),
        "lines": [line("企业定位", 160), line("城市产业链智能创新生态运营商", 560)],
    }
    assert _block_text(authored)[0] == "企业定位 城市产业链智能创新生态运营商"

    # English keeps the space either way: there the break really is one.
    latin = {
        "type": 0,
        "bbox": (60, 100, 560, 134),
        "lines": [line("a wrapped english", 550), line("sentence.", 300)],
    }
    assert _block_text(latin)[0] == "a wrapped english sentence."
