"""Slide style extraction — the Chromium renderer is only as good as this."""

from __future__ import annotations

from pathlib import Path

import pytest

from doc2video.tools.slides import ChromiumSlideRenderer, extract_deck, load_theme
from doc2video.tools.slides.model import Geometry, ShapeKind
from doc2video.tools.slides.theme import Theme, apply_brightness

from .conftest import DEMO_PAGE_COUNT


@pytest.fixture
def deck(demo_pptx: Path, tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    return extract_deck(demo_pptx, assets, target_width=1920)


# -- theme ----------------------------------------------------------------


def test_apply_brightness_tints_and_shades():
    assert apply_brightness("#808080", 0.0) == "#808080"
    # Positive brightness moves toward white, negative toward black.
    assert apply_brightness("#000000", 1.0) == "#FFFFFF"
    assert apply_brightness("#FFFFFF", -1.0) == "#000000"


def test_theme_falls_back_to_office_defaults():
    theme = Theme()
    assert theme.slot("accent1") == "#4472C4"
    assert theme.slot("lt1") == "#FFFFFF"


def test_load_theme_reads_the_deck_scheme(demo_pptx: Path):
    theme = load_theme(demo_pptx)
    # Every slot must resolve to a concrete colour, never to a scheme name.
    for slot in ("dk1", "lt1", "accent1", "hlink"):
        assert theme.slot(slot).startswith("#")
        assert len(theme.slot(slot)) == 7


# -- geometry -------------------------------------------------------------


def test_deck_dimensions_and_scale(deck):
    assert deck.width == 1920
    # 10 x 7.5 inch slide → 4:3.
    assert deck.height == 1440
    # 1920px over 10in at 72pt/in.
    assert deck.pt_to_px == pytest.approx(1920 / 10 / 72, rel=1e-6)
    assert len(deck.slides) == DEMO_PAGE_COUNT


def test_shapes_stay_inside_the_slide(deck):
    for slide in deck.slides:
        for shape in slide.shapes:
            assert shape.box.w > 0 and shape.box.h > 0
            assert -1 <= shape.box.x <= deck.width + 1
            assert -1 <= shape.box.y <= deck.height + 1


def test_corner_radius_comes_from_the_shape_not_a_constant(deck):
    rounded = [
        s
        for slide in deck.slides
        for s in slide.shapes
        if s.style.geometry is Geometry.ROUND_RECT
    ]
    assert rounded, "示例 deck 应包含圆角矩形"
    for shape in rounded:
        # OOXML default adjustment is 1/6 of the shorter side.
        expected = min(shape.box.w, shape.box.h) / 6
        assert shape.style.corner_radius_px == pytest.approx(expected, rel=0.02)


# -- styling --------------------------------------------------------------


def test_explicit_colours_survive_extraction(deck):
    fills = {s.style.fill for slide in deck.slides for s in slide.shapes if s.style.fill}
    # The accent band and the panel from scripts/make_demo.py.
    assert "#C46A46" in fills
    assert "#F4F1EA" in fills


def test_run_styling_is_preserved(deck):
    titles = [
        run
        for slide in deck.slides
        for shape in slide.shapes
        if shape.text
        for para in shape.text.paragraphs
        for run in para.runs
        if run.bold
    ]
    assert titles, "标题应保留粗体"
    assert all(run.size_pt for run in titles)
    assert any(run.color == "#1C2433" for run in titles)


def test_alignment_is_preserved(deck):
    aligns = {
        para.align.value
        for slide in deck.slides
        for shape in slide.shapes
        if shape.text
        for para in shape.text.paragraphs
    }
    assert "right" in aligns, "页码是右对齐的"


def test_bullets_are_not_invented_for_plain_text_boxes(deck):
    """A plain text box inherits no bullet — inventing one diverges from PowerPoint."""
    bullets = [
        para.bullet
        for slide in deck.slides
        for shape in slide.shapes
        if shape.text
        for para in shape.text.paragraphs
    ]
    assert bullets, "示例 deck 应有段落"
    assert all(b == "" for b in bullets)


# -- tables ---------------------------------------------------------------


def test_table_is_extracted_with_a_default_style(deck):
    tables = [s for slide in deck.slides for s in slide.shapes if s.kind is ShapeKind.TABLE]
    assert len(tables) == 1
    table = tables[0].table

    assert [c.text for c in table.rows[0]] == ["指标", "本月", "环比"]
    assert len(table.col_widths) == 3
    assert sum(table.col_widths) == pytest.approx(tables[0].box.w, rel=0.02)
    # PowerPoint always applies a table style; a plain grid would be less faithful.
    assert table.header_fill is not None
    assert table.band_fill is not None


# -- renderer wiring -------------------------------------------------------


def test_chromium_renderer_reports_why_it_is_unavailable(tmp_path: Path):
    renderer = ChromiumSlideRenderer(renderer_dir=tmp_path / "no-renderer")
    assert renderer.available() is False
    assert renderer.unavailable_reason()


def test_chromium_render_of_empty_deck_is_a_no_op(deck, tmp_path: Path):
    deck.slides = []
    assert ChromiumSlideRenderer().render(deck, tmp_path) == []
