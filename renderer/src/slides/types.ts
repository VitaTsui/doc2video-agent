/**
 * Slide render model — mirrors `doc2video/tools/slides/model.py`.
 *
 * Style only: nothing here feeds narration or camera work, so it can grow
 * (gradients, rotation, tables) without touching the video pipeline.
 */

export type Align = "left" | "center" | "right" | "justify";
export type VAnchor = "top" | "middle" | "bottom";
export type ShapeKind = "text" | "picture" | "table" | "chart" | "auto";

export type ChartKind =
  | "column"
  | "column_stacked"
  | "bar"
  | "bar_stacked"
  | "line"
  | "line_markers"
  | "area"
  | "area_stacked"
  | "pie"
  | "doughnut"
  | "scatter"
  | "other";
export type Geometry = "rect" | "roundRect" | "ellipse" | "line" | "other";

export type Box = { x: number; y: number; w: number; h: number };

export type Run = {
  text: string;
  bold: boolean;
  italic: boolean;
  underline: boolean;
  /** null means the run inherits — the renderer applies `default_size_pt`. */
  size_pt: number | null;
  color: string | null;
  font: string | null;
};

export type Paragraph = {
  runs: Run[];
  align: Align;
  level: number;
  bullet: string;
  line_spacing: number;
  space_before_pt: number;
  space_after_pt: number;
};

export type TextBody = {
  paragraphs: Paragraph[];
  v_anchor: VAnchor;
  wrap: boolean;
  margin_left_pt: number;
  margin_right_pt: number;
  margin_top_pt: number;
  margin_bottom_pt: number;
  default_size_pt: number;
};

export type ShapeStyle = {
  /** A hex colour or a full CSS `linear-gradient(...)` string. */
  fill: string | null;
  line_color: string | null;
  line_width_px: number;
  geometry: Geometry;
  rotation: number;
  corner_radius_px: number;
};

export type TableCell = {
  text: string;
  bold: boolean;
  align: Align;
  fill: string | null;
};

export type TableData = {
  rows: TableCell[][];
  col_widths: number[];
  row_heights: number[];
  /** Approximation of PowerPoint's default banded table style. */
  header_fill: string | null;
  band_fill: string | null;
  border_color: string;
  header_text: string;
};

export type ChartSeries = {
  name: string;
  /** null is a real gap in the data — never draw through it. */
  values: (number | null)[];
  color: string;
};

export type ChartData = {
  kind: ChartKind;
  title: string;
  categories: string[];
  series: ChartSeries[];
  /** One colour per slice for pie/doughnut; empty for cartesian charts. */
  point_colors: string[];
  has_legend: boolean;
  legend_position: string;
  has_data_labels: boolean;
  gridlines: boolean;
  y_min: number | null;
  y_max: number | null;
};

export type SlideShape = {
  kind: ShapeKind;
  box: Box;
  style: ShapeStyle;
  text: TextBody | null;
  /** Path relative to `public/`, staged by the Python side. */
  image: string | null;
  table: TableData | null;
  chart: ChartData | null;
};

export type Slide = {
  index: number;
  background: string;
  shapes: SlideShape[];
};

export type SlideDeck = {
  width: number;
  height: number;
  pt_to_px: number;
  slides: Slide[];
};

/** Font stack used when a deck's font is unavailable in the render container. */
export const FONT_STACK =
  '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Source Han Sans SC", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

export const fontFamily = (name: string | null): string =>
  name ? `"${name}", ${FONT_STACK}` : FONT_STACK;
