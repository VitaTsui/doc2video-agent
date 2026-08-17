import React from "react";
import { fontFamily, type ChartData, type ChartKind, type ChartSeries } from "./types";

/**
 * Charts drawn as plain SVG.
 *
 * This reproduces a chart that already exists in the deck rather than designing
 * one, so the palette comes from `chart.series[].color` (the deck's own series
 * fills, or its theme accents) and data labels appear only when the deck turned
 * them on. What is applied from general practice is everything the file does
 * *not* specify: recessive gridlines, ink-coloured text, thin marks with rounded
 * data ends, a legend only when more than one series needs identifying.
 *
 * Two shapes here exist purely for fidelity, against general practice:
 *
 * - **A secondary axis.** Two y-scales in one frame is the classic misleading
 *   chart, but a deck that has one is *read* against it — flattening the series
 *   onto the primary scale would put a percentage line along the floor of a
 *   chart running to thousands, which misrepresents the slide more than the
 *   dual axis misrepresents the data.
 * - **3D extrusion.** Also an anti-pattern, also what the audience is looking
 *   at. The data is plotted flat and the depth is drawn as a face on top, so
 *   the geometry stays honest while the frame still matches the slide.
 */

const INK = "#1F2430";
const INK_MUTED = "#6B7280";
const GRID = "rgba(31, 36, 48, 0.12)";
const AXIS = "rgba(31, 36, 48, 0.28)";
/** Keeps adjacent fills from reading as one shape. */
const SURFACE_GAP = 2;
const BAR_RADIUS = 4;
const LINE_WIDTH = 2;
const MARKER_RADIUS = 4;
/** Depth of the 3D face, as a share of the bar's own width. */
const DEPTH_RATIO = 0.32;
const MAX_DEPTH = 12;

type Layout = {
  left: number;
  top: number;
  width: number;
  height: number;
};

type Scale = { min: number; max: number; step: number };

const isStackedKind = (kind: ChartKind) => kind.endsWith("_stacked");
const isAreaKind = (kind: ChartKind) => kind === "area" || kind === "area_stacked";
const isPathKind = (kind: ChartKind) =>
  kind === "line" || kind === "line_markers" || kind === "scatter" || isAreaKind(kind);

/** The plot type this series is drawn as: its own, or the chart's. */
const kindOf = (series: ChartSeries, chart: ChartData): ChartKind =>
  series.kind ?? chart.kind;

/** "Nice" axis bounds and step, so ticks land on readable numbers. */
const niceScale = (min: number, max: number, ticks = 4): Scale => {
  if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) {
    return { min: 0, max: max || 1, step: (max || 1) / ticks };
  }
  const raw = (max - min) / ticks;
  const magnitude = Math.pow(10, Math.floor(Math.log10(Math.abs(raw) || 1)));
  const normalized = raw / magnitude;
  const step =
    (normalized >= 5 ? 10 : normalized >= 2 ? 5 : normalized >= 1 ? 2 : 1) *
    magnitude;
  return {
    min: Math.floor(min / step) * step,
    max: Math.ceil(max / step) * step,
    step,
  };
};

const formatValue = (value: number): string => {
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(abs >= 10_000 ? 0 : 1)}k`;
  if (Number.isInteger(value)) return String(value);
  // A secondary axis often carries rates, where 0.4 must not print as "0".
  return abs < 1 ? value.toFixed(2) : value.toFixed(1);
};

const stackedTotals = (series: ChartSeries[], count: number): number[] =>
  Array.from({ length: count }, (_, i) =>
    series.reduce((sum, s) => sum + (s.values[i] ?? 0), 0),
  );

/** Lighten (positive) or darken (negative) a `#RRGGBB` for the 3D faces. */
const shade = (color: string, amount: number): string => {
  const value = color.replace("#", "");
  if (value.length !== 6) return color;
  const channels = [0, 2, 4].map((i) => parseInt(value.slice(i, i + 2), 16));
  if (channels.some((c) => Number.isNaN(c))) return color;
  const mixed = channels.map((c) =>
    Math.round(amount >= 0 ? c + (255 - c) * amount : c * (1 + amount)),
  );
  return `#${mixed
    .map((c) => Math.max(0, Math.min(255, c)).toString(16).padStart(2, "0"))
    .join("")}`;
};

export const Chart: React.FC<{
  chart: ChartData;
  width: number;
  height: number;
  ptToPx: number;
}> = ({ chart, width, height, ptToPx }) => {
  const fontSize = Math.max(11, 10 * ptToPx);
  const isRound = chart.kind === "pie" || chart.kind === "doughnut";
  const isHorizontal = chart.kind === "bar" || chart.kind === "bar_stacked";
  const hasSecondary =
    !isRound && chart.y2_visible && chart.series.some((s) => s.secondary_axis);

  const titleHeight = chart.title ? fontSize * 2.2 : fontSize * 0.6;
  const legendHeight = chart.has_legend ? fontSize * 2.2 : 0;

  const gutter = fontSize * 3.4;
  const rightGutter = hasSecondary ? gutter : fontSize;
  const plot: Layout = {
    left: isRound ? 0 : gutter,
    top: titleHeight,
    width: width - (isRound ? 0 : gutter + rightGutter),
    height: height - titleHeight - legendHeight - (isRound ? 0 : fontSize * 2),
  };

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      style={{ fontFamily: fontFamily(null), display: "block" }}
    >
      {chart.title ? (
        <text
          x={width / 2}
          y={fontSize * 1.3}
          textAnchor="middle"
          fill={INK}
          fontSize={fontSize * 1.15}
          fontWeight={600}
        >
          {chart.title}
        </text>
      ) : null}

      {isRound ? (
        <RoundPlot chart={chart} plot={plot} fontSize={fontSize} />
      ) : (
        <CartesianPlot
          chart={chart}
          plot={plot}
          fontSize={fontSize}
          isHorizontal={isHorizontal}
          hasSecondary={hasSecondary}
        />
      )}

      {chart.has_legend ? (
        <Legend
          items={legendItems(chart, isRound)}
          width={width}
          y={height - legendHeight / 2}
          fontSize={fontSize}
        />
      ) : null}
    </svg>
  );
};

// ---------------------------------------------------------------------------
// cartesian: column / bar / line / area / scatter, and combinations of them
// ---------------------------------------------------------------------------

const CartesianPlot: React.FC<{
  chart: ChartData;
  plot: Layout;
  fontSize: number;
  isHorizontal: boolean;
  hasSecondary: boolean;
}> = ({ chart, plot, fontSize, isHorizontal, hasSecondary }) => {
  const count = Math.max(
    chart.categories.length,
    ...chart.series.map((s) => s.values.length),
    1,
  );

  const onPrimary = chart.series.filter((s) => !s.secondary_axis);
  const onSecondary = chart.series.filter((s) => s.secondary_axis);

  /** Bounds across a set of series, stacking only what is actually stacked. */
  const boundsOf = (list: ChartSeries[]) => {
    const stacked = list.filter((s) => isStackedKind(kindOf(s, chart)));
    const plain = list.filter((s) => !isStackedKind(kindOf(s, chart)));
    const values = [
      ...(stacked.length ? stackedTotals(stacked, count) : []),
      ...plain.flatMap((s) => s.values.filter((v): v is number => v !== null)),
    ];
    return {
      min: Math.min(0, ...(values.length ? values : [0])),
      max: Math.max(0, ...(values.length ? values : [1])),
    };
  };

  const primaryBounds = boundsOf(onPrimary.length ? onPrimary : chart.series);
  const primary = niceScale(
    chart.y_min ?? primaryBounds.min,
    chart.y_max ?? primaryBounds.max,
  );
  const secondaryBounds = boundsOf(onSecondary);
  const secondary = niceScale(
    chart.y2_min ?? secondaryBounds.min,
    chart.y2_max ?? secondaryBounds.max,
  );

  // Along = the category direction; across = the value direction.
  const alongSize = isHorizontal ? plot.height : plot.width;
  const acrossSize = isHorizontal ? plot.width : plot.height;
  const pxOn = (scale: Scale) => (v: number) =>
    ((v - scale.min) / (scale.max - scale.min || 1)) * acrossSize;
  const scaleFor = (series: ChartSeries) =>
    pxOn(series.secondary_axis ? secondary : primary);

  const band = alongSize / count;
  // Only bar-shaped series claim a slot in the band; a line laid over columns
  // runs through the middle of them rather than beside them.
  const barSeries = chart.series.filter((s) => !isPathKind(kindOf(s, chart)));
  const stackedBars = barSeries.filter((s) => isStackedKind(kindOf(s, chart)));
  const groupCount = Math.max(
    barSeries.length - stackedBars.length + (stackedBars.length ? 1 : 0),
    1,
  );
  // Leave ~30% of the band as breathing room between category groups.
  const barWidth = Math.max(2, (band * 0.7) / groupCount);
  const depth = chart.three_d ? Math.min(barWidth * DEPTH_RATIO, MAX_DEPTH) : 0;

  const ticks: number[] = [];
  for (let v = primary.min; v <= primary.max + primary.step / 2; v += primary.step) {
    ticks.push(Number(v.toFixed(6)));
  }
  /** The secondary value sharing a gridline with this primary tick. */
  const secondaryAt = (tick: number) => {
    const share = (tick - primary.min) / (primary.max - primary.min || 1);
    return secondary.min + share * (secondary.max - secondary.min);
  };

  /** Slot within the band, for bar-shaped series only. */
  const slotOf = (series: ChartSeries): number => {
    let slot = 0;
    for (const other of barSeries) {
      if (other === series) return slot;
      if (!isStackedKind(kindOf(other, chart))) slot += 1;
    }
    return slot;
  };

  return (
    <g>
      {/* Gridlines stay recessive: they orient, they do not compete. */}
      {chart.gridlines
        ? ticks.map((tick, i) => {
            const offset = pxOn(primary)(tick);
            return isHorizontal ? (
              <line
                key={i}
                x1={plot.left + offset}
                y1={plot.top}
                x2={plot.left + offset}
                y2={plot.top + plot.height}
                stroke={GRID}
                strokeWidth={1}
              />
            ) : (
              <line
                key={i}
                x1={plot.left}
                y1={plot.top + plot.height - offset}
                x2={plot.left + plot.width}
                y2={plot.top + plot.height - offset}
                stroke={GRID}
                strokeWidth={1}
              />
            );
          })
        : null}

      {/* Value axis labels */}
      {ticks.map((tick, i) => {
        const offset = pxOn(primary)(tick);
        return isHorizontal ? (
          <text
            key={i}
            x={plot.left + offset}
            y={plot.top + plot.height + fontSize * 1.3}
            textAnchor="middle"
            fill={INK_MUTED}
            fontSize={fontSize * 0.95}
          >
            {formatValue(tick)}
          </text>
        ) : (
          <text
            key={i}
            x={plot.left - fontSize * 0.5}
            y={plot.top + plot.height - offset + fontSize * 0.35}
            textAnchor="end"
            fill={INK_MUTED}
            fontSize={fontSize * 0.95}
          >
            {formatValue(tick)}
          </text>
        );
      })}

      {/* The second scale, labelled on the right so each series can be read
          against the axis it was actually plotted on. */}
      {hasSecondary && !isHorizontal
        ? ticks.map((tick, i) => (
            <text
              key={`s-${i}`}
              x={plot.left + plot.width + fontSize * 0.5}
              y={plot.top + plot.height - pxOn(primary)(tick) + fontSize * 0.35}
              textAnchor="start"
              fill={INK_MUTED}
              fontSize={fontSize * 0.95}
            >
              {formatValue(secondaryAt(tick))}
            </text>
          ))
        : null}

      <line
        x1={plot.left}
        y1={plot.top + plot.height}
        x2={plot.left + plot.width}
        y2={plot.top + plot.height}
        stroke={AXIS}
        strokeWidth={1}
      />

      {/* Category labels */}
      {chart.categories.map((label, i) =>
        isHorizontal ? (
          <text
            key={i}
            x={plot.left - fontSize * 0.5}
            y={plot.top + band * (i + 0.5) + fontSize * 0.35}
            textAnchor="end"
            fill={INK_MUTED}
            fontSize={fontSize * 0.9}
          >
            {label}
          </text>
        ) : (
          <text
            key={i}
            x={plot.left + band * (i + 0.5)}
            y={plot.top + plot.height + fontSize * 1.4}
            textAnchor="middle"
            fill={INK_MUTED}
            fontSize={fontSize * 0.9}
          >
            {label}
          </text>
        ),
      )}

      {/* Bars first, paths over them: a combo chart's line belongs on top. */}
      {chart.series.map((series, si) => {
        const kind = kindOf(series, chart);
        if (isPathKind(kind)) return null;
        const stacked = isStackedKind(kind);
        const valueToPx = scaleFor(series);
        return (
          <g key={`b-${si}`}>
            {series.values.slice(0, count).map((value, ci) => {
              if (value === null) return null;
              const below = stacked
                ? chart.series
                    .slice(0, si)
                    .filter((s) => isStackedKind(kindOf(s, chart)))
                    .reduce((sum, s) => sum + (s.values[ci] ?? 0), 0)
                : 0;
              const length = Math.abs(valueToPx(value + below) - valueToPx(below));
              const start = valueToPx(below);
              const alongPos =
                band * (ci + 0.5) -
                (barWidth * groupCount) / 2 +
                slotOf(series) * barWidth;

              const thickness = Math.max(1, barWidth - SURFACE_GAP);
              const extent = Math.max(1, length - (stacked ? SURFACE_GAP : 0));
              const x = isHorizontal ? plot.left + start : plot.left + alongPos;
              const y = isHorizontal
                ? plot.top + alongPos
                : plot.top + plot.height - start - length;
              const w = isHorizontal ? extent : thickness;
              const h = isHorizontal ? thickness : extent;

              return (
                <Bar
                  key={`${si}-${ci}`}
                  x={x}
                  y={y}
                  width={w}
                  height={h}
                  color={series.color}
                  radius={depth ? 0 : Math.min(BAR_RADIUS, barWidth / 3)}
                  depth={depth}
                />
              );
            })}
          </g>
        );
      })}

      {chart.series.map((series, si) => {
        const kind = kindOf(series, chart);
        if (!isPathKind(kind)) return null;
        return (
          <SeriesPath
            key={`p-${si}`}
            series={series}
            index={si}
            chart={chart}
            plot={plot}
            band={band}
            valueToPx={scaleFor(series)}
            isArea={isAreaKind(kind)}
            isStacked={isStackedKind(kind)}
            showMarkers={kind !== "line" && kind !== "area"}
          />
        );
      })}

      {/* Data labels only when the deck asked for them. */}
      {chart.has_data_labels
        ? chart.series.map((series, si) => {
            if (isStackedKind(kindOf(series, chart))) return null;
            const valueToPx = scaleFor(series);
            const isPath = isPathKind(kindOf(series, chart));
            return series.values.slice(0, count).map((value, ci) => {
              if (value === null) return null;
              const alongPos = isPath
                ? band * (ci + 0.5)
                : band * (ci + 0.5) -
                  (barWidth * groupCount) / 2 +
                  slotOf(series) * barWidth +
                  barWidth / 2;
              const across = valueToPx(value);
              return isHorizontal ? (
                <text
                  key={`l-${si}-${ci}`}
                  x={plot.left + across + fontSize * 0.4}
                  y={plot.top + alongPos + fontSize * 0.35}
                  fill={INK}
                  fontSize={fontSize * 0.85}
                >
                  {formatValue(value)}
                </text>
              ) : (
                <text
                  key={`l-${si}-${ci}`}
                  x={plot.left + alongPos}
                  y={plot.top + plot.height - across - fontSize * 0.4}
                  textAnchor="middle"
                  fill={INK}
                  fontSize={fontSize * 0.85}
                >
                  {formatValue(value)}
                </text>
              );
            });
          })
        : null}
    </g>
  );
};

/**
 * One bar, optionally extruded.
 *
 * The depth is drawn as two lighter/darker faces going up and to the right, so
 * the front face still starts at the baseline and ends at the true value — the
 * geometry a reader measures stays exactly as tall as the number.
 */
const Bar: React.FC<{
  x: number;
  y: number;
  width: number;
  height: number;
  color: string;
  radius: number;
  depth: number;
}> = ({ x, y, width, height, color, radius, depth }) => {
  if (!depth) {
    return (
      <rect x={x} y={y} width={width} height={height} rx={radius} fill={color} />
    );
  }
  const top = `M ${x} ${y} L ${x + depth} ${y - depth} L ${x + width + depth} ${
    y - depth
  } L ${x + width} ${y} Z`;
  const side = `M ${x + width} ${y} L ${x + width + depth} ${y - depth} L ${
    x + width + depth
  } ${y + height - depth} L ${x + width} ${y + height} Z`;
  return (
    <g>
      <path d={side} fill={shade(color, -0.25)} />
      <path d={top} fill={shade(color, 0.22)} />
      <rect x={x} y={y} width={width} height={height} fill={color} />
    </g>
  );
};

const SeriesPath: React.FC<{
  series: ChartSeries;
  index: number;
  chart: ChartData;
  plot: Layout;
  band: number;
  valueToPx: (v: number) => number;
  isArea: boolean;
  isStacked: boolean;
  showMarkers: boolean;
}> = ({
  series,
  index,
  chart,
  plot,
  band,
  valueToPx,
  isArea,
  isStacked,
  showMarkers,
}) => {
  const points: Array<{ x: number; y: number } | null> = series.values.map(
    (value, i) => {
      if (value === null) return null;
      const below = isStacked
        ? chart.series
            .slice(0, index)
            .filter((s) => isStackedKind(kindOf(s, chart)))
            .reduce((sum, s) => sum + (s.values[i] ?? 0), 0)
        : 0;
      return {
        x: plot.left + band * (i + 0.5),
        y: plot.top + plot.height - valueToPx(value + below),
      };
    },
  );

  // A null is a gap in the data; drawing through it would invent a value.
  const segments: Array<Array<{ x: number; y: number }>> = [];
  let current: Array<{ x: number; y: number }> = [];
  for (const point of points) {
    if (point === null) {
      if (current.length) segments.push(current);
      current = [];
    } else {
      current.push(point);
    }
  }
  if (current.length) segments.push(current);

  return (
    <g>
      {isArea
        ? segments.map((segment, si) => (
            <path
              key={`a-${si}`}
              d={`M ${segment[0].x} ${plot.top + plot.height} ${segment
                .map((p) => `L ${p.x} ${p.y}`)
                .join(" ")} L ${segment[segment.length - 1].x} ${
                plot.top + plot.height
              } Z`}
              fill={series.color}
              fillOpacity={0.85}
            />
          ))
        : null}

      {!isArea
        ? segments.map((segment, si) => (
            <polyline
              key={`p-${si}`}
              points={segment.map((p) => `${p.x},${p.y}`).join(" ")}
              fill="none"
              stroke={series.color}
              strokeWidth={LINE_WIDTH}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          ))
        : null}

      {showMarkers
        ? points.map((point, i) =>
            point ? (
              <circle
                key={`m-${i}`}
                cx={point.x}
                cy={point.y}
                r={MARKER_RADIUS}
                fill={series.color}
                stroke="#FFFFFF"
                strokeWidth={SURFACE_GAP}
              />
            ) : null,
          )
        : null}
    </g>
  );
};

// ---------------------------------------------------------------------------
// pie / doughnut
// ---------------------------------------------------------------------------

const RoundPlot: React.FC<{
  chart: ChartData;
  plot: Layout;
  fontSize: number;
}> = ({ chart, plot, fontSize }) => {
  // A pie shows one series split by category, so slice colour tracks category.
  const values = (chart.series[0]?.values ?? []).map((v) => v ?? 0);
  const total = values.reduce((sum, v) => sum + Math.abs(v), 0) || 1;
  const cx = plot.left + plot.width / 2;
  const cy = plot.top + plot.height / 2;
  const radius = Math.max(4, Math.min(plot.width, plot.height) / 2 - fontSize);
  const inner = chart.kind === "doughnut" ? radius * 0.55 : 0;
  // A 3D pie is tilted and given a rim; the angles stay untouched, so the
  // proportions a reader takes from it are the proportions in the data.
  const depth = chart.three_d ? Math.min(radius * 0.16, MAX_DEPTH) : 0;
  const squash = depth ? 0.72 : 1;

  const slice = (start: number, end: number, r: number, ri: number) => {
    const large = end - start > Math.PI ? 1 : 0;
    const px = (angle: number, rr: number) => cx + rr * Math.cos(angle);
    const py = (angle: number, rr: number) => cy + rr * squash * Math.sin(angle);
    return (
      `M ${px(start, ri)} ${py(start, ri)} L ${px(start, r)} ${py(start, r)} ` +
      `A ${r} ${r * squash} 0 ${large} 1 ${px(end, r)} ${py(end, r)} ` +
      `L ${px(end, ri)} ${py(end, ri)} ` +
      (ri > 0
        ? `A ${ri} ${ri * squash} 0 ${large} 0 ${px(start, ri)} ${py(start, ri)}`
        : "") +
      " Z"
    );
  };

  const angles: Array<{ start: number; end: number }> = [];
  let angle = -Math.PI / 2;
  for (const value of values) {
    const sweep = (Math.abs(value) / total) * Math.PI * 2;
    angles.push({ start: angle, end: angle + sweep });
    angle += sweep;
  }

  return (
    <g>
      {/* The rim: the same slices, dropped by the depth and darkened. */}
      {depth
        ? angles.map(({ start, end }, i) => (
            <path
              key={`d-${i}`}
              d={slice(start, end, radius, inner)}
              transform={`translate(0, ${depth})`}
              fill={shade(sliceColor(chart, i), -0.3)}
            />
          ))
        : null}

      {angles.map(({ start, end }, i) => (
        <path
          key={i}
          d={slice(start, end, radius, inner)}
          fill={sliceColor(chart, i)}
          stroke="#FFFFFF"
          strokeWidth={SURFACE_GAP}
        />
      ))}
    </g>
  );
};

const sliceColor = (chart: ChartData, index: number): string => {
  // Assigned per slice by the extractor, following PowerPoint's accent order.
  if (chart.point_colors.length) {
    return chart.point_colors[index % chart.point_colors.length];
  }
  return chart.series[index % Math.max(chart.series.length, 1)]?.color ?? "#4472C4";
};

type LegendItem = { label: string; color: string };

const legendItems = (chart: ChartData, isRound: boolean): LegendItem[] =>
  isRound
    ? chart.categories.map((label, i) => ({ label, color: sliceColor(chart, i) }))
    : chart.series.map((s) => ({ label: s.name, color: s.color }));

// ---------------------------------------------------------------------------

const Legend: React.FC<{
  items: LegendItem[];
  width: number;
  y: number;
  fontSize: number;
}> = ({ items, width, y, fontSize }) => {
  // No text metrics in SVG layout, so estimate: CJK glyphs are ~1em wide,
  // latin ~0.55em. Good enough to centre the run without overlap.
  const textWidth = (label: string) =>
    [...label].reduce(
      (sum, ch) => sum + (ch.charCodeAt(0) > 0x2e7f ? 1 : 0.55) * fontSize * 0.95,
      0,
    );

  const swatch = fontSize * 0.8;
  const gap = fontSize * 0.5;
  const itemGap = fontSize * 1.6;
  const widths = items.map((item) => swatch + gap + textWidth(item.label));
  const total =
    widths.reduce((sum, w) => sum + w, 0) + itemGap * Math.max(items.length - 1, 0);

  let cursor = (width - total) / 2;
  return (
    <g>
      {items.map((item, i) => {
        const x = cursor;
        cursor += widths[i] + itemGap;
        return (
          <g key={i} transform={`translate(${x}, ${y})`}>
            <rect
              x={0}
              y={-swatch / 2}
              width={swatch}
              height={swatch}
              rx={2}
              fill={item.color}
            />
            {/* Text keeps ink colour; the swatch beside it carries identity. */}
            <text
              x={swatch + gap}
              y={fontSize * 0.34}
              fill={INK_MUTED}
              fontSize={fontSize * 0.95}
            >
              {item.label}
            </text>
          </g>
        );
      })}
    </g>
  );
};
