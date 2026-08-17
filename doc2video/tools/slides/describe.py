"""Turn a chart into words the narration skill can actually use.

A chart is usually the one thing on a slide worth zooming into, but the parser
used to hand the writer only "图表" plus a list of category names. That is not
enough to say anything true about it, so the script ends up describing the page
instead of the data — exactly the "AI 只是读 PPT" failure the project is trying
to avoid (方案 §20).

This produces a compact factual summary — direction, endpoints, magnitude of
change, the dominant share — and states nothing the numbers do not support.
"""

from __future__ import annotations

from .model import ChartData, ChartKind

KIND_NAMES = {
    ChartKind.COLUMN: "柱状图",
    ChartKind.COLUMN_STACKED: "堆叠柱状图",
    ChartKind.BAR: "条形图",
    ChartKind.BAR_STACKED: "堆叠条形图",
    ChartKind.LINE: "折线图",
    ChartKind.LINE_MARKERS: "折线图",
    ChartKind.AREA: "面积图",
    ChartKind.AREA_STACKED: "堆叠面积图",
    ChartKind.PIE: "饼图",
    ChartKind.DOUGHNUT: "环形图",
    ChartKind.SCATTER: "散点图",
    ChartKind.OTHER: "图表",
}

# Below this the change is noise, not a story worth narrating.
FLAT_THRESHOLD = 0.05


def describe_chart(chart: ChartData) -> str:
    """One-line factual summary of a chart's data."""
    kind = KIND_NAMES.get(chart.kind, "图表")
    head = f"{kind}「{chart.title}」" if chart.title else kind

    if chart.kind in (ChartKind.PIE, ChartKind.DOUGHNUT):
        return f"{head}：{_describe_shares(chart)}"

    span = _describe_span(chart)
    series_parts = [_describe_series(s.name, s.values) for s in chart.series]
    series_parts = [p for p in series_parts if p]
    if not series_parts:
        return head
    return f"{head}：{span}；" + "；".join(series_parts)


def _describe_span(chart: ChartData) -> str:
    if not chart.categories:
        return "无类别标签"
    if len(chart.categories) == 1:
        return chart.categories[0]
    return f"{chart.categories[0]} 至 {chart.categories[-1]}"


def _describe_series(name: str, values: list[float | None]) -> str:
    numbers = [v for v in values if v is not None]
    if not numbers:
        return ""
    first, last = numbers[0], numbers[-1]
    peak = max(numbers)
    label = name or "该系列"

    if len(numbers) == 1:
        return f"{label} {_number(first)}"

    change = _relative_change(first, last)
    if change is None:
        trend = "变化"
    elif abs(change) < FLAT_THRESHOLD:
        return f"{label} 基本持平（约 {_number(last)}）"
    else:
        trend = "增长" if change > 0 else "下降"

    text = f"{label} 从 {_number(first)} {trend}到 {_number(last)}"
    if change is not None and abs(change) >= FLAT_THRESHOLD:
        text += f"（{change:+.0%}）"
    # A peak that is not the endpoint is the part a viewer would ask about.
    if peak > max(first, last) * 1.05:
        text += f"，峰值 {_number(peak)}"
    return text


def _describe_shares(chart: ChartData) -> str:
    values = [v or 0 for v in (chart.series[0].values if chart.series else [])]
    total = sum(abs(v) for v in values)
    if not values or total == 0:
        return "无数据"

    pairs = list(zip(chart.categories or [f"第 {i + 1} 项" for i in range(len(values))],
                     values, strict=False))
    pairs.sort(key=lambda item: -abs(item[1]))
    top = pairs[: min(3, len(pairs))]
    parts = [f"{label} 占 {abs(value) / total:.0%}" for label, value in top]
    return "、".join(parts)


def _relative_change(first: float, last: float) -> float | None:
    if first == 0:
        return None
    return (last - first) / abs(first)


def _number(value: float) -> str:
    if abs(value) < 1 and value != 0:
        # Values under 1 are usually shares; percent reads better than 0.62.
        return f"{value:.0%}"
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.1f}"
