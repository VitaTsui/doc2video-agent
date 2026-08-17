"""Slide *render* model — style, not meaning.

Deliberately separate from ``schemas/document.py``: that model answers "what is
this page about" and is what the director reasons over; this one answers "what
does this page look like" and is only ever consumed by a slide renderer.

Keeping them apart means adding gradients or rotation here can never change how
narration or camera work behaves.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ShapeKind(StrEnum):
    TEXT = "text"
    PICTURE = "picture"
    TABLE = "table"
    CHART = "chart"
    AUTO = "auto"


class ChartKind(StrEnum):
    COLUMN = "column"
    COLUMN_STACKED = "column_stacked"
    BAR = "bar"
    BAR_STACKED = "bar_stacked"
    LINE = "line"
    LINE_MARKERS = "line_markers"
    AREA = "area"
    AREA_STACKED = "area_stacked"
    PIE = "pie"
    DOUGHNUT = "doughnut"
    SCATTER = "scatter"
    OTHER = "other"


class Geometry(StrEnum):
    RECT = "rect"
    ROUND_RECT = "roundRect"
    ELLIPSE = "ellipse"
    LINE = "line"
    OTHER = "other"


class Align(StrEnum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    JUSTIFY = "justify"


class VAnchor(StrEnum):
    TOP = "top"
    MIDDLE = "middle"
    BOTTOM = "bottom"


class Box(BaseModel):
    """Position in rendered pixels, matching the page image's coordinate space."""

    x: float
    y: float
    w: float
    h: float


class TextEffects(BaseModel):
    """WordArt: the parts of a run's look that are not just a colour."""

    outline_color: str | None = None
    outline_width_px: float = 0.0
    # A CSS gradient painted through the glyphs instead of a solid colour.
    gradient: str | None = None
    # A ready-made CSS ``text-shadow`` value (shadow and glow both land here).
    shadow: str | None = None


class Run(BaseModel):
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    # None means "inherited" — the renderer applies the paragraph default.
    size_pt: float | None = None
    color: str | None = None
    font: str | None = None
    effects: TextEffects | None = None


class Paragraph(BaseModel):
    runs: list[Run] = Field(default_factory=list)
    align: Align = Align.LEFT
    level: int = 0
    bullet: str = ""
    line_spacing: float = 1.2
    space_before_pt: float = 0.0
    space_after_pt: float = 0.0


class TextBody(BaseModel):
    paragraphs: list[Paragraph] = Field(default_factory=list)
    v_anchor: VAnchor = VAnchor.TOP
    wrap: bool = True
    margin_left_pt: float = 7.2
    margin_right_pt: float = 7.2
    margin_top_pt: float = 3.6
    margin_bottom_pt: float = 3.6
    # Fallback size when a run inherits from the layout, which we do not resolve.
    default_size_pt: float = 18.0


class ShapeStyle(BaseModel):
    fill: str | None = None
    line_color: str | None = None
    line_width_px: float = 0.0
    geometry: Geometry = Geometry.RECT
    rotation: float = 0.0
    corner_radius_px: float = 0.0


class TableCell(BaseModel):
    text: str = ""
    bold: bool = False
    align: Align = Align.LEFT
    fill: str | None = None


class TableData(BaseModel):
    rows: list[list[TableCell]] = Field(default_factory=list)
    col_widths: list[float] = Field(default_factory=list)
    row_heights: list[float] = Field(default_factory=list)
    # Resolved from ppt/tableStyles.xml when the deck defines the style it names;
    # otherwise these carry a theme-accent approximation of the default look.
    header_fill: str | None = None
    band_fill: str | None = None
    border_color: str = "#FFFFFF"
    header_text: str = "#FFFFFF"


class ChartSeries(BaseModel):
    name: str = ""
    # None is a genuine gap in the data, not zero — the renderer must not join across it.
    values: list[float | None] = Field(default_factory=list)
    color: str = "#4472C4"
    # Combo charts mix plot types in one chart; None means "same as the chart".
    kind: ChartKind | None = None
    # Series on the secondary axis have their own scale, usually because their
    # magnitude is nowhere near the primary one's.
    secondary_axis: bool = False


class ChartData(BaseModel):
    """A chart faithful to the deck, not redesigned.

    Colours come from the deck's own series formatting or, when it inherits,
    from the theme accents in PowerPoint's own cycling order. Re-palettizing
    would make the video disagree with the slide it came from.
    """

    kind: ChartKind = ChartKind.COLUMN
    title: str = ""
    categories: list[str] = Field(default_factory=list)
    series: list[ChartSeries] = Field(default_factory=list)
    # Pie and doughnut colour by *category*, not by series — one colour per slice,
    # taken from the deck's per-point formatting or its theme accents.
    point_colors: list[str] = Field(default_factory=list)
    has_legend: bool = False
    legend_position: str = "bottom"
    # Honour the deck's choice: labelling every point is the deck's call, not ours.
    has_data_labels: bool = False
    gridlines: bool = True
    y_min: float | None = None
    y_max: float | None = None
    # Secondary value axis, present only when some series sits on it.
    y2_min: float | None = None
    y2_max: float | None = None
    y2_visible: bool = True
    # PowerPoint's 3D chart types. The data is plotted flat — a rotated 3D plot
    # is harder to read, which is why it is an anti-pattern — but the extruded
    # look is kept so the frame still matches the slide it came from.
    three_d: bool = False


class SlideShape(BaseModel):
    kind: ShapeKind = ShapeKind.TEXT
    box: Box
    style: ShapeStyle = Field(default_factory=ShapeStyle)
    text: TextBody | None = None
    # Path relative to the renderer's public directory, set when staging assets.
    image: str | None = None
    table: TableData | None = None
    chart: ChartData | None = None


class Slide(BaseModel):
    index: int
    background: str = "#FFFFFF"
    # z-order is the list order, exactly as the shape tree gives it.
    shapes: list[SlideShape] = Field(default_factory=list)


class SlideDeck(BaseModel):
    width: int
    height: int
    # Font sizes stay in points (what the file actually stores); the renderer
    # multiplies by this to reach pixels at the chosen render width.
    pt_to_px: float = 1.0
    slides: list[Slide] = Field(default_factory=list)
