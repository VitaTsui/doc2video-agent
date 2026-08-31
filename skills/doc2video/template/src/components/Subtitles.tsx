import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";

import { layout, palette, typography } from "../design-system";
import type { SceneSegment } from "../compositions/generated-scenes";

/**
 * The line being spoken, drawn on the canvas.
 *
 * Drawn here rather than burnt in by ffmpeg afterwards, which is how the
 * page-based pipeline does it. Two reasons: the segment timings come straight
 * from the speech engine, so the caption changes exactly when the sentence
 * does; and a missing CJK font shows up as a wrong-looking preview rather than
 * as a finished video full of hollow boxes.
 *
 * One sentence at a time. A caption that carries the whole paragraph is a
 * caption nobody reads, and it covers the scene it is supposed to accompany.
 */
export const Subtitles: React.FC<{ segments: SceneSegment[] }> = ({ segments }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const now = frame / fps;

  const current = segments.find((s) => now >= s.start && now < s.end);
  if (!current || !current.text.trim()) return null;

  return (
    <div
      style={{
        position: "absolute",
        left: layout.margin,
        right: layout.margin,
        bottom: 72,
        display: "flex",
        justifyContent: "center",
      }}
    >
      <span
        style={{
          maxWidth: "100%",
          padding: "14px 28px",
          borderRadius: layout.radius,
          backgroundColor: "rgba(42, 39, 36, 0.82)",
          color: palette.cream,
          fontFamily: typography.sans,
          fontSize: typography.scale.caption + 6,
          fontWeight: typography.weight.medium,
          lineHeight: typography.lineHeight.normal,
          textAlign: "center",
          textWrap: "balance",
        }}
      >
        {current.text}
      </span>
    </div>
  );
};
