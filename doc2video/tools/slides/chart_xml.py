"""The chart XML python-pptx does not expose.

``PlotFactory`` handles nine plot tags. Everything else — ``bar3DChart``,
``line3DChart``, ``pie3DChart``, ``ofPieChart``, ``surface3DChart`` — raises
``unsupported plot type``, so ``chart.series`` comes back empty and the chart
is dropped from the slide entirely. A 3D column chart is ordinary in a
corporate deck, and a blank rectangle where one used to be is the worst
possible outcome: the narration still talks about a chart nobody can see.

Three things are read straight from the plot area, all in document order so
they line up index-for-index with ``chart.series`` when that does work:

* **series data** — names, values (with gaps preserved) and explicit colours,
  which is the fallback that makes unsupported plot types render at all.
* **per-series plot kind** — a ``<c:barChart>`` and a ``<c:lineChart>`` can
  share a plot area. Reported as one type, the line series get drawn as bars.
* **axis assignment** — a group pointing at a second value axis is on its own
  scale; plotting it against the primary one lays a "38% margin" line flat
  along the floor of a chart whose other series run to thousands.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import ChartKind
from .theme import Theme

C_NS = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

# Plot-group tag -> the render kind it produces, before grouping is applied.
GROUP_KINDS = {
    "barChart": ChartKind.COLUMN,
    "bar3DChart": ChartKind.COLUMN,
    "lineChart": ChartKind.LINE,
    "line3DChart": ChartKind.LINE,
    "areaChart": ChartKind.AREA,
    "area3DChart": ChartKind.AREA,
    "pieChart": ChartKind.PIE,
    "pie3DChart": ChartKind.PIE,
    "ofPieChart": ChartKind.PIE,
    "doughnutChart": ChartKind.DOUGHNUT,
    "scatterChart": ChartKind.SCATTER,
    "bubbleChart": ChartKind.SCATTER,
}

THREE_D_TAGS = {"bar3DChart", "line3DChart", "area3DChart", "pie3DChart", "surface3DChart"}

STACKED_KINDS = {
    ChartKind.COLUMN: ChartKind.COLUMN_STACKED,
    ChartKind.BAR: ChartKind.BAR_STACKED,
    ChartKind.AREA: ChartKind.AREA_STACKED,
}


@dataclass(frozen=True)
class AxisScale:
    """A value axis' fixed bounds, and whether the deck shows it at all."""

    minimum: float | None = None
    maximum: float | None = None
    visible: bool = True


@dataclass(frozen=True)
class RawSeries:
    """One ``<c:ser>``, for the plot types python-pptx refuses to open."""

    name: str = ""
    values: list[float | None] = field(default_factory=list)
    color: str | None = None


@dataclass
class PlotGroups:
    """Per-series facts, indexed the same way ``chart.series`` is."""

    kinds: list[ChartKind] = field(default_factory=list)
    secondary: list[bool] = field(default_factory=list)
    series: list[RawSeries] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    primary_scale: AxisScale | None = None
    secondary_scale: AxisScale | None = None
    three_d: bool = False

    @property
    def has_secondary(self) -> bool:
        return any(self.secondary)

    def kind_at(self, index: int) -> ChartKind | None:
        return self.kinds[index] if index < len(self.kinds) else None

    def secondary_at(self, index: int) -> bool:
        return self.secondary[index] if index < len(self.secondary) else False


def read_plot_groups(chart, theme: Theme) -> PlotGroups:
    """Walk the plot area, reading every series and the axis it belongs to."""
    plot_area = _plot_area(chart)
    if plot_area is None:
        return PlotGroups()

    value_axes = {
        _axis_id(node): node for node in plot_area.findall(f"{C_NS}valAx") if _axis_id(node)
    }

    groups = PlotGroups()
    primary_axis: str | None = None
    secondary_axis: str | None = None

    for node in plot_area:
        tag = node.tag.replace(C_NS, "")
        base = GROUP_KINDS.get(tag)
        if base is None:
            continue

        kind = _group_kind(node, base)
        axis_id = _value_axis_id(node, value_axes)
        if primary_axis is None:
            primary_axis = axis_id
        is_secondary = axis_id is not None and axis_id != primary_axis
        if is_secondary and secondary_axis is None:
            secondary_axis = axis_id

        if tag in THREE_D_TAGS:
            groups.three_d = True

        for item in node.findall(f"{C_NS}ser"):
            groups.kinds.append(kind)
            groups.secondary.append(is_secondary)
            groups.series.append(_read_series(item, theme))
            if not groups.categories:
                groups.categories = _read_categories(item)

    # Which axis is "the" value axis is ambiguous to python-pptx once a chart
    # has two: it answers with one of them, and reading a combo chart's bars
    # against the percentage axis flattens every bar to the ceiling.
    if primary_axis is not None:
        groups.primary_scale = _scale_of(value_axes[primary_axis])
    if secondary_axis is not None:
        groups.secondary_scale = _scale_of(value_axes[secondary_axis])
    return groups


# --------------------------------------------------------------------------
# series data
# --------------------------------------------------------------------------


def _read_series(node, theme: Theme) -> RawSeries:
    return RawSeries(
        name=_series_name(node),
        values=_cached_numbers(node.find(f"{C_NS}val")),
        color=_series_color(node, theme),
    )


def _series_name(node) -> str:
    tx = node.find(f"{C_NS}tx")
    if tx is None:
        return ""
    for point in tx.iter(f"{C_NS}v"):
        if point.text:
            return point.text.strip()
    return ""


def _series_color(node, theme: Theme) -> str | None:
    sp_pr = node.find(f"{C_NS}spPr")
    if sp_pr is None:
        return None
    solid = sp_pr.find(f"{A_NS}solidFill")
    return theme.color_element(solid) if solid is not None else None


def _read_categories(node) -> list[str]:
    cat = node.find(f"{C_NS}cat")
    if cat is None:
        return []
    labels: dict[int, str] = {}
    count = 0
    for cache in (f"{C_NS}strCache", f"{C_NS}numCache", f"{C_NS}multiLvlStrCache"):
        for holder in cat.iter(cache):
            count = max(count, _point_count(holder))
            for point in holder.findall(f"{C_NS}pt"):
                index = _index_of(point)
                value = point.find(f"{C_NS}v")
                if index is not None and value is not None and value.text:
                    labels.setdefault(index, value.text.strip())
    if not labels:
        return []
    total = max(count, max(labels) + 1)
    return [labels.get(i, "") for i in range(total)]


def _cached_numbers(container) -> list[float | None]:
    """Values in index order, with missing points left as genuine gaps."""
    if container is None:
        return []
    numbers: dict[int, float] = {}
    count = 0
    for cache in container.iter(f"{C_NS}numCache"):
        count = max(count, _point_count(cache))
        for point in cache.findall(f"{C_NS}pt"):
            index = _index_of(point)
            value = point.find(f"{C_NS}v")
            if index is None or value is None or not value.text:
                continue
            try:
                numbers[index] = float(value.text)
            except ValueError:
                continue
    if not numbers and not count:
        return []
    total = max(count, max(numbers) + 1 if numbers else 0)
    return [numbers.get(i) for i in range(total)]


def _point_count(cache) -> int:
    node = cache.find(f"{C_NS}ptCount")
    if node is None or node.get("val") is None:
        return 0
    try:
        return int(node.get("val"))
    except ValueError:
        return 0


def _index_of(point) -> int | None:
    try:
        return int(point.get("idx", ""))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# groups and axes
# --------------------------------------------------------------------------


def _plot_area(chart):
    try:
        root = chart._chartSpace
    except AttributeError:
        return None
    return root.find(f"{C_NS}chart/{C_NS}plotArea")


def _group_kind(node, base: ChartKind) -> ChartKind:
    if base is ChartKind.COLUMN and _attr_of(node, "barDir") == "bar":
        base = ChartKind.BAR

    grouping = _attr_of(node, "grouping")
    if grouping in ("stacked", "percentStacked"):
        return STACKED_KINDS.get(base, base)

    if base is ChartKind.LINE and _has_markers(node):
        return ChartKind.LINE_MARKERS
    return base


def _has_markers(node) -> bool:
    """A line group draws markers unless every series switches them off."""
    for item in node.findall(f"{C_NS}ser"):
        marker = item.find(f"{C_NS}marker")
        if marker is None:
            continue
        symbol = marker.find(f"{C_NS}symbol")
        if symbol is not None and symbol.get("val") not in (None, "none"):
            return True
    return False


def _value_axis_id(node, value_axes: dict) -> str | None:
    """Which value axis this group plots against.

    A group lists every axis it uses — category, value, and for 3D a series
    axis — so the value axis is the one that matches a declared ``valAx``.
    """
    for ref in node.findall(f"{C_NS}axId"):
        axis_id = ref.get("val")
        if axis_id in value_axes:
            return axis_id
    return None


def _axis_id(node) -> str | None:
    ref = node.find(f"{C_NS}axId")
    return ref.get("val") if ref is not None else None


def _scale_of(node) -> AxisScale:
    scaling = node.find(f"{C_NS}scaling")
    minimum = maximum = None
    if scaling is not None:
        minimum = _float_of(scaling, "min")
        maximum = _float_of(scaling, "max")
    delete = node.find(f"{C_NS}delete")
    visible = delete is None or delete.get("val") in ("0", "false")
    return AxisScale(minimum=minimum, maximum=maximum, visible=visible)


def _float_of(parent, tag: str) -> float | None:
    node = parent.find(f"{C_NS}{tag}")
    if node is None or node.get("val") is None:
        return None
    try:
        return float(node.get("val"))
    except ValueError:
        return None


def _attr_of(parent, tag: str) -> str | None:
    node = parent.find(f"{C_NS}{tag}")
    return node.get("val") if node is not None else None
