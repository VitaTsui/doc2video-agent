import React from "react";
import { AbsoluteFill, interpolate } from "remotion";

import { Chart } from "../slides/Chart";
import type { ChartData, ChartKind } from "../slides/types";
import type { PlanChart } from "../types";

/**
 * A chart drawn again, over the one already printed on the page.
 *
 * The page image was rasterised from this same data by this same `Chart`
 * component, so the drawing underneath and the drawing on top are the same
 * picture. That is what makes animating it safe: the bars grow, and when they
 * stop they are sitting exactly on the bars in the image. Nothing to
 * disagree with, nothing that can drift out of step with the slide.
 *
 * It exists because a chart is the one thing on a deck where zooming is a poor
 * substitute for explaining. "Revenue went from 120 to 250" wants the bars to
 * arrive in that order; a camera push on a finished bar chart just shows the
 * answer larger. The numbers come from the OOXML, so no part of this is a
 * guess about what the slide says (方案 §12).
 *
 * Covering rather than replacing: the printed chart stays underneath, and the
 * live one fades in over it. If anything here fails to draw, the frame still
 * has the chart the deck had.
 */
export const LiveChart: React.FC<{
  chart: PlanChart;
  time: number;
  /** Frame size, so the chart is drawn at the pixel size it occupies. */
  frameWidth: number;
  frameHeight: number;
}> = ({ chart, time, frameWidth, frameHeight }) => {
  const grow = Math.max(chart.grow, 0.001);
  // Before its moment the printed chart is what shows; there is nothing to add.
  if (time < chart.start) {
    return null;
  }

  const progress = interpolate(time, [chart.start, chart.start + grow], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  // Eased, because a bar that arrives at constant speed reads as a progress
  // bar rather than as a value settling.
  const eased = 1 - Math.pow(1 - progress, 3);

  // The axis is fixed to the finished numbers. Growing the values alone makes
  // the scale grow with them: the bars sit at 42% of a ceiling that is also at
  // 42%, so the chart re-scales and nothing appears to move. Pinning the top
  // to the real maximum is what turns "the numbers change" into "the bars
  // rise", and it is also what the printed chart underneath is drawn against.
  const highest = Math.max(
    0,
    ...chart.series.flatMap((series) =>
      series.values.filter((value): value is number => value !== null),
    ),
  );
  const lowest = Math.min(
    0,
    ...chart.series.flatMap((series) =>
      series.values.filter((value): value is number => value !== null),
    ),
  );
  const round = chart.kind === "pie" || chart.kind === "doughnut";

  const data: ChartData = {
    kind: chart.kind as ChartKind,
    title: chart.title,
    categories: chart.categories,
    series: chart.series.map((series) => ({
      name: series.name,
      color: series.color || "#4472C4",
      kind: null,
      secondary_axis: false,
      // Growing the values rather than scaling the drawing: the axis stays
      // put and the gridlines stay where the printed chart has them, so the
      // two pictures line up throughout, not only at the end.
      values: series.values.map((value) =>
        value === null ? null : value * eased,
      ),
    })),
    point_colors: [],
    has_legend: chart.series.length > 1,
    legend_position: "bottom",
    has_data_labels: false,
    gridlines: true,
    // Round charts have no axis to pin, and a slice growing from nothing is
    // already legible.
    y_min: round ? null : lowest,
    y_max: round ? null : highest,
    y2_min: null,
    y2_max: null,
    y2_visible: true,
    three_d: false,
  };

  return (
    <AbsoluteFill
      style={{
        left: `${chart.area.x * 100}%`,
        top: `${chart.area.y * 100}%`,
        width: `${chart.area.w * 100}%`,
        height: `${chart.area.h * 100}%`,
        // Hides the printed chart while this one grows. Without it both are on
        // screen at once and the bars read as cut off rather than rising —
        // the colour is sampled from the page's own corners, so on a finished
        // frame there is nothing to see.
        backgroundColor: chart.backdrop || "#ffffff",
      }}
    >
      <Chart
        chart={data}
        width={chart.area.w * frameWidth}
        height={chart.area.h * frameHeight}
        // The page was rasterised at its own scale; here the same chart has to
        // land on the same pixels of a 1080p frame, so type is sized against
        // the frame rather than against the slide's points.
        ptToPx={(chart.area.h * frameHeight) / 260}
      />
    </AbsoluteFill>
  );
};
