import React from "react";
import { fontFamily, type ChartData, type ChartSeries } from "./types";

/**
 * Charts drawn as plain SVG.
 *
 * This reproduces a chart that already exists in the deck rather than designing
 * one, so the palette comes from `chart.series[].color` (the deck's own series
 * fills, or its theme accents) and data labels appear only when the deck turned
 * them on. What is applied from general practice is everything the file does
 * *not* specify: recessive gridlines, ink-coloured text, thin marks with rounded
 * data ends, a legend only when more than one series needs identifying.
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

type Layout = {
  left: number;
  top: number;
  width: number;
  height: number;
};

/** "Nice" axis bounds and step, so ticks land on readable numbers. */
const niceScale = (min: number, max: number, ticks = 4) => {
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
  return value.toFixed(1);
};

const stackedTotals = (series: ChartSeries[], count: number): number[] =>
  Array.from({ length: count }, (_, i) =>
    series.reduce((sum, s) => sum + (s.values[i] ?? 0), 0),
  );

export const Chart: React.FC<{
  chart: ChartData;
  width: number;
  height: number;
  ptToPx: number;
}> = ({ chart, width, height, ptToPx }) => {
  const fontSize = Math.max(11, 10 * ptToPx);
  const isStacked =
    chart.kind === "column_stacked" ||
    chart.kind === "bar_stacked" ||
    chart.kind === "area_stacked";
  const isRound = chart.kind === "pie" || chart.kind === "doughnut";
  const isHorizontal = chart.kind === "bar" || chart.kind === "bar_stacked";

  const titleHeight = chart.title ? fontSize * 2.2 : fontSize * 0.6;
  const legendHeight = chart.has_legend ? fontSize * 2.2 : 0;

  const plot: Layout = {
    left: isRound ? 0 : fontSize * 3.4,
    top: titleHeight,
    width: width - (isRound ? 0 : fontSize * 3.4) - fontSize,
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
          isStacked={isStacked}
          isHorizontal={isHorizontal}
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
// cartesian: column / bar / line / area / scatter
// ---------------------------------------------------------------------------

const CartesianPlot: React.FC<{
  chart: ChartData;
  plot: Layout;
  fontSize: number;
  isStacked: boolean;
  isHorizontal: boolean;
}> = ({ chart, plot, fontSize, isStacked, isHorizontal }) => {
  const count = Math.max(
    chart.categories.length,
    ...chart.series.map((s) => s.values.length),
    1,
  );

  const flat = chart.series.flatMap((s) =>
    s.values.filter((v): v is number => v !== null),
  );
  const totals = isStacked ? stackedTotals(chart.series, count) : flat;
  const dataMin = Math.min(0, ...(totals.length ? totals : [0]));
  const dataMax = Math.max(0, ...(totals.length ? totals : [1]));
  const scale = niceScale(chart.y_min ?? dataMin, chart.y_max ?? dataMax);

  // Along = the category direction; across = the value direction.
  const alongSize = isHorizontal ? plot.height : plot.width;
  const acrossSize = isHorizontal ? plot.width : plot.height;
  const valueToPx = (v: number) =>
    ((v - scale.min) / (scale.max - scale.min || 1)) * acrossSize;

  const band = alongSize / count;
  const groupCount = isStacked ? 1 : chart.series.length || 1;
  // Leave ~30% of the band as breathing room between category groups.
  const barWidth = Math.max(2, (band * 0.7) / groupCount);

  const ticks: number[] = [];
  for (let v = scale.min; v <= scale.max + scale.step / 2; v += scale.step) {
    ticks.push(Number(v.toFixed(6)));
  }

  const isLineLike =
    chart.kind === "line" ||
    chart.kind === "line_markers" ||
    chart.kind === "scatter";
  const isArea = chart.kind === "area" || chart.kind === "area_stacked";

  return (
    <g>
      {/* Gridlines stay recessive: they orient, they do not compete. */}
      {chart.gridlines
        ? ticks.map((tick, i) => {
            const offset = valueToPx(tick);
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
        const offset = valueToPx(tick);
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

      {isLineLike || isArea
        ? chart.series.map((series, si) => (
            <SeriesPath
              key={si}
              series={series}
              index={si}
              chart={chart}
              plot={plot}
              band={band}
              valueToPx={valueToPx}
              isArea={isArea}
              isStacked={isStacked}
              showMarkers={chart.kind !== "line" && chart.kind !== "area"}
              fontSize={fontSize}
            />
          ))
        : chart.series.map((series, si) =>
            series.values.slice(0, count).map((value, ci) => {
              if (value === null) return null;
              const below = isStacked
                ? chart.series
                    .slice(0, si)
                    .reduce((sum, s) => sum + (s.values[ci] ?? 0), 0)
                : 0;
              const length = Math.abs(valueToPx(value + below) - valueToPx(below));
              const start = valueToPx(below);
              const alongPos =
                band * (ci + 0.5) -
                (barWidth * groupCount) / 2 +
                (isStacked ? 0 : si * barWidth);

              return isHorizontal ? (
                <rect
                  key={`${si}-${ci}`}
                  x={plot.left + start}
                  y={plot.top + alongPos + (isStacked ? 0 : 0)}
                  width={Math.max(1, length - (isStacked ? SURFACE_GAP : 0))}
                  height={Math.max(1, barWidth - SURFACE_GAP)}
                  rx={Math.min(BAR_RADIUS, barWidth / 3)}
                  fill={series.color}
                />
              ) : (
                <rect
                  key={`${si}-${ci}`}
                  x={plot.left + alongPos}
                  y={plot.top + plot.height - start - length}
                  width={Math.max(1, barWidth - SURFACE_GAP)}
                  height={Math.max(1, length - (isStacked ? SURFACE_GAP : 0))}
                  rx={Math.min(BAR_RADIUS, barWidth / 3)}
                  fill={series.color}
                />
              );
            }),
          )}

      {/* Data labels only when the deck asked for them. */}
      {chart.has_data_labels && !isStacked
        ? chart.series.map((series, si) =>
            series.values.slice(0, count).map((value, ci) => {
              if (value === null) return null;
              const alongPos =
                band * (ci + 0.5) -
                (barWidth * groupCount) / 2 +
                si * barWidth +
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
            }),
          )
        : null}
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
  fontSize: number;
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

  let angle = -Math.PI / 2;
  return (
    <g>
      {values.map((value, i) => {
        const sweep = (Math.abs(value) / total) * Math.PI * 2;
        const start = angle;
        const end = angle + sweep;
        angle = end;

        const large = sweep > Math.PI ? 1 : 0;
        const x0 = cx + radius * Math.cos(start);
        const y0 = cy + radius * Math.sin(start);
        const x1 = cx + radius * Math.cos(end);
        const y1 = cy + radius * Math.sin(end);

        const outer = `M ${cx + inner * Math.cos(start)} ${
          cy + inner * Math.sin(start)
        } L ${x0} ${y0} A ${radius} ${radius} 0 ${large} 1 ${x1} ${y1} L ${
          cx + inner * Math.cos(end)
        } ${cy + inner * Math.sin(end)} ${
          inner > 0
            ? `A ${inner} ${inner} 0 ${large} 0 ${cx + inner * Math.cos(start)} ${
                cy + inner * Math.sin(start)
              }`
            : ""
        } Z`;

        return (
          <path
            key={i}
            d={outer}
            fill={sliceColor(chart, i)}
            stroke="#FFFFFF"
            strokeWidth={SURFACE_GAP}
          />
        );
      })}
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
