import React from "react";
import { Img, staticFile } from "remotion";
import { Chart } from "./Chart";
import {
  fontFamily,
  type Paragraph,
  type SlideShape,
  type TextBody,
} from "./types";

const isGradient = (fill: string) => fill.startsWith("linear-gradient");

const anchorToJustify = (anchor: TextBody["v_anchor"]) =>
  anchor === "middle" ? "center" : anchor === "bottom" ? "flex-end" : "flex-start";

/** Bullet indent per outline level, in points (PowerPoint's default rhythm). */
const INDENT_PT = 18;

const ParagraphView: React.FC<{
  paragraph: Paragraph;
  body: TextBody;
  ptToPx: number;
}> = ({ paragraph, body, ptToPx }) => {
  const size = (paragraph.runs[0]?.size_pt ?? body.default_size_pt) * ptToPx;
  const indent = paragraph.level * INDENT_PT * ptToPx;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "baseline",
        justifyContent:
          paragraph.align === "center"
            ? "center"
            : paragraph.align === "right"
              ? "flex-end"
              : "flex-start",
        textAlign: paragraph.align,
        marginLeft: indent,
        marginTop: paragraph.space_before_pt * ptToPx,
        marginBottom: paragraph.space_after_pt * ptToPx,
        lineHeight: paragraph.line_spacing,
      }}
    >
      {paragraph.bullet ? (
        <span
          style={{
            flex: "0 0 auto",
            marginRight: size * 0.4,
            fontSize: size,
            color: paragraph.runs[0]?.color ?? "inherit",
          }}
        >
          {paragraph.bullet}
        </span>
      ) : null}
      <span style={{ flex: "0 1 auto" }}>
        {paragraph.runs.map((run, index) => (
          <span
            key={index}
            style={{
              fontSize: (run.size_pt ?? body.default_size_pt) * ptToPx,
              fontWeight: run.bold ? 700 : 400,
              fontStyle: run.italic ? "italic" : "normal",
              textDecoration: run.underline ? "underline" : "none",
              color: run.color ?? undefined,
              fontFamily: fontFamily(run.font),
              whiteSpace: "pre-wrap",
            }}
          >
            {run.text}
          </span>
        ))}
      </span>
    </div>
  );
};

/**
 * One PowerPoint shape as a positioned div.
 *
 * Rotation is applied around the shape's own centre, which is how OOXML defines
 * it — rotating around the frame instead would scatter every rotated label.
 */
export const Shape: React.FC<{ shape: SlideShape; ptToPx: number }> = ({
  shape,
  ptToPx,
}) => {
  const { box, style } = shape;

  const outer: React.CSSProperties = {
    position: "absolute",
    left: box.x,
    top: box.y,
    width: box.w,
    height: box.h,
    transform: style.rotation ? `rotate(${style.rotation}deg)` : undefined,
    boxSizing: "border-box",
  };

  const surface: React.CSSProperties = {
    width: "100%",
    height: "100%",
    boxSizing: "border-box",
    background: style.fill ?? undefined,
    border: style.line_color
      ? `${Math.max(1, style.line_width_px)}px solid ${style.line_color}`
      : undefined,
    borderRadius:
      style.geometry === "ellipse"
        ? "50%"
        : style.geometry === "roundRect"
          ? style.corner_radius_px
          : undefined,
    overflow: "hidden",
  };

  if (shape.kind === "picture" && shape.image) {
    return (
      <div style={outer}>
        <Img
          src={staticFile(shape.image)}
          style={{ ...surface, objectFit: "fill" }}
        />
      </div>
    );
  }

  if (shape.kind === "chart" && shape.chart) {
    return (
      <div style={outer}>
        <div style={{ ...surface, background: style.fill ?? "transparent" }}>
          <Chart
            chart={shape.chart}
            width={box.w}
            height={box.h}
            ptToPx={ptToPx}
          />
        </div>
      </div>
    );
  }

  if (shape.kind === "table" && shape.table) {
    const { rows, col_widths, header_fill, band_fill, border_color, header_text } =
      shape.table;
    return (
      <div style={outer}>
        <table
          style={{
            ...surface,
            borderCollapse: "collapse",
            tableLayout: "fixed",
            fontFamily: fontFamily(null),
            fontSize: 14 * ptToPx,
          }}
        >
          <tbody>
            {rows.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => {
                  const isHeader = r === 0 && header_fill !== null;
                  // An explicit cell fill always wins over the style default.
                  const background =
                    cell.fill ??
                    (isHeader
                      ? header_fill
                      : r % 2 === 1
                        ? (band_fill ?? undefined)
                        : undefined);
                  return (
                    <td
                      key={c}
                      style={{
                        width: col_widths[c],
                        border: `1px solid ${border_color}`,
                        padding: 6 * ptToPx,
                        textAlign: cell.align,
                        fontWeight: isHeader || cell.bold ? 700 : 400,
                        color: isHeader ? header_text : undefined,
                        background,
                        verticalAlign: "middle",
                      }}
                    >
                      {cell.text}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  const body = shape.text;
  return (
    <div style={outer}>
      <div
        style={{
          ...surface,
          // A gradient must go through `background`, a flat colour through
          // `backgroundColor` — mixing them silently drops the gradient.
          background: style.fill && isGradient(style.fill) ? style.fill : undefined,
          backgroundColor:
            style.fill && !isGradient(style.fill) ? style.fill : undefined,
          display: "flex",
          flexDirection: "column",
          justifyContent: body ? anchorToJustify(body.v_anchor) : "flex-start",
          paddingLeft: (body?.margin_left_pt ?? 0) * ptToPx,
          paddingRight: (body?.margin_right_pt ?? 0) * ptToPx,
          paddingTop: (body?.margin_top_pt ?? 0) * ptToPx,
          paddingBottom: (body?.margin_bottom_pt ?? 0) * ptToPx,
          whiteSpace: body?.wrap === false ? "nowrap" : "normal",
          fontFamily: fontFamily(null),
          color: "#000000",
        }}
      >
        {body?.paragraphs.map((paragraph, index) => (
          <ParagraphView
            key={index}
            paragraph={paragraph}
            body={body}
            ptToPx={ptToPx}
          />
        ))}
      </div>
    </div>
  );
};
