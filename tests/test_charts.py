"""Chart extraction and description.

Charts are the thing a viewer most often needs zoomed into, so they have to
survive both paths: as pixels (the render model) and as words (what the
narration skill is allowed to say about them).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doc2video.skills.document import _truncate
from doc2video.tools.slides import describe_chart, extract_deck
from doc2video.tools.slides.model import ChartData, ChartKind, ChartSeries, ShapeKind


@pytest.fixture
def charts(demo_pptx: Path, tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    deck = extract_deck(demo_pptx, assets, target_width=1920)
    return [s for slide in deck.slides for s in slide.shapes if s.kind is ShapeKind.CHART]


# -- extraction ------------------------------------------------------------


def test_demo_deck_yields_a_column_and_a_pie(charts):
    kinds = {shape.chart.kind for shape in charts}
    assert ChartKind.COLUMN in kinds
    assert ChartKind.PIE in kinds


def test_column_chart_keeps_categories_and_values(charts):
    column = next(s.chart for s in charts if s.chart.kind is ChartKind.COLUMN)
    assert column.categories == ["Q1", "Q2", "Q3", "Q4"]
    assert len(column.series) == 2
    assert column.series[0].values == [1200, 2100, 3400, 5200]
    # Two series need identifying, and the deck asked for a legend.
    assert column.has_legend is True
    assert column.gridlines is True


def test_series_get_distinct_theme_colours(charts):
    column = next(s.chart for s in charts if s.chart.kind is ChartKind.COLUMN)
    colours = [s.color for s in column.series]
    assert len(set(colours)) == len(colours)
    assert all(c.startswith("#") and len(c) == 7 for c in colours)


def test_pie_colours_by_slice_not_by_series(charts):
    pie = next(s.chart for s in charts if s.chart.kind is ChartKind.PIE)
    # One series, three slices — identity lives on the categories.
    assert len(pie.series) == 1
    assert len(pie.point_colors) == len(pie.categories) == 3
    assert len(set(pie.point_colors)) == 3
    # A pie without a legend is unreadable, so the deck's setting must survive.
    assert pie.has_legend is True


# -- description -----------------------------------------------------------


def test_describe_growth_reports_direction_and_magnitude():
    chart = ChartData(
        kind=ChartKind.COLUMN,
        categories=["Q1", "Q4"],
        series=[ChartSeries(name="营收", values=[100.0, 400.0])],
    )
    text = describe_chart(chart)
    assert "增长" in text and "100" in text and "400" in text and "+300%" in text


def test_describe_decline():
    chart = ChartData(
        kind=ChartKind.LINE,
        categories=["1月", "6月"],
        series=[ChartSeries(name="流失率", values=[20.0, 5.0])],
    )
    assert "下降" in describe_chart(chart)


def test_describe_flat_series_is_not_called_a_trend():
    chart = ChartData(
        kind=ChartKind.LINE,
        categories=["A", "B", "C"],
        series=[ChartSeries(name="稳定值", values=[100.0, 101.0, 102.0])],
    )
    text = describe_chart(chart)
    assert "持平" in text
    assert "增长" not in text


def test_describe_peak_is_mentioned_when_it_is_not_the_endpoint():
    chart = ChartData(
        kind=ChartKind.LINE,
        categories=["A", "B", "C"],
        series=[ChartSeries(name="访问量", values=[100.0, 900.0, 200.0])],
    )
    assert "峰值" in describe_chart(chart)


def test_describe_pie_reports_shares():
    chart = ChartData(
        kind=ChartKind.PIE,
        categories=["A", "B", "C"],
        series=[ChartSeries(name="占比", values=[0.5, 0.3, 0.2])],
    )
    text = describe_chart(chart)
    assert "50%" in text and "30%" in text


def test_describe_handles_gaps_without_inventing_values():
    chart = ChartData(
        kind=ChartKind.LINE,
        categories=["A", "B", "C"],
        series=[ChartSeries(name="断点", values=[10.0, None, 30.0])],
    )
    text = describe_chart(chart)
    assert "10" in text and "30" in text


def test_describe_empty_chart_does_not_crash():
    assert describe_chart(ChartData(kind=ChartKind.COLUMN))


def test_chart_element_text_carries_numbers(demo_pptx: Path, tmp_path: Path):
    """The semantic parser must expose the numbers, not just the word 图表."""
    from doc2video.tools.parsers import parse

    document = parse(demo_pptx, tmp_path / "a", target_width=1280)
    chart_texts = [
        el.text
        for page in document.pages
        for el in page.elements
        if el.kind.value == "chart"
    ]
    assert chart_texts
    assert any(any(ch.isdigit() for ch in text) for text in chart_texts)


# -- truncation ------------------------------------------------------------


def test_truncate_never_splits_a_thousands_separator():
    text = "生成视频数 从 1,200 增长到 5,200；复用次数 从 300 增长到 3,100（+933%）"
    for limit in range(10, len(text)):
        out = _truncate(text, limit)
        assert not out.endswith(","), f"limit={limit} 切断了数字：{out!r}"


def test_truncate_prefers_a_clause_boundary():
    assert _truncate("第一句；第二句；第三句", 6).endswith("；")


def test_truncate_leaves_short_text_alone():
    assert _truncate("短", 60) == "短"


def test_a_chart_the_narrator_points_at_is_carried_into_the_plan(settings, store, demo_pptx):
    """A chart is the one thing on a deck where zooming explains nothing.

    "Revenue went from 120 to 250" wants the bars to arrive in that order; a
    camera push on a finished bar chart shows the answer larger. The numbers
    come from the OOXML, so redrawing cannot change what the slide says — that
    is the only reason a rebuilt chart is allowed to move at all.
    """
    from doc2video.agent import Doc2VideoAgent
    from doc2video.agent.executor import Executor
    from doc2video.agent.planner import Stage
    from doc2video.skills.base import SkillContext
    from doc2video.skills.motion import MotionSkill

    agent = Doc2VideoAgent(settings, store)
    project = agent.create_project(demo_pptx)
    plan = agent.planner.initial_plan("讲清楚这几页的数据", project)
    plan.stages = [s for s in plan.stages if s is not Stage.RENDER]
    ctx = SkillContext.build(project, store=store, settings=settings)
    project = Executor(ctx).run(plan, message="test")

    charted = [
        element
        for page in project.document.pages
        for element in page.elements
        if element.chart is not None
    ]
    assert charted, "样例文稿里有图表，解析时应当把数字一起留下"
    assert all(element.chart.categories for element in charted)
    assert all(element.chart.series for element in charted)

    # And the colours are the deck's own, resolved against its theme — the
    # page image was rasterised from the same numbers by the same component,
    # so a live redraw that used a different palette would visibly disagree
    # with the chart printed underneath it.
    assert all(
        series.color.startswith("#")
        for element in charted
        for series in element.chart.series
    )

    plans = {plan.scene_id: plan for plan in MotionSkill(ctx).scene_plans()}
    with_charts = [p for p in plans.values() if p.charts]
    if with_charts:  # only when the director happened to point at one
        chart = with_charts[0].charts[0]
        assert chart.categories and chart.series
        assert chart.backdrop.startswith("#")
        assert chart.start >= 0
