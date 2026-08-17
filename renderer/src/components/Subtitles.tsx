import React from "react";
import { AbsoluteFill } from "remotion";
import type { PlanSubtitle } from "../types";

/**
 * Subtitles render outside the camera layer so they stay legible and fixed
 * while the page zooms. One cue at a time — overlapping cues would stack.
 */
export const Subtitles: React.FC<{
  cues: PlanSubtitle[];
  time: number;
  /** Gap below the box as a fraction of frame height, from the plan. */
  margin: number;
  height: number;
}> = ({ cues, time, margin, height }) => {
  const active = cues.find((cue) => time >= cue.start && time < cue.end);
  if (!active) {
    return null;
  }

  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-end",
        alignItems: "center",
        // In pixels off the frame height: a percentage here would resolve
        // against the frame's *width*, which is how the caption used to sit
        // 115px up on a 1080p frame while the ffmpeg renderer put it at 75.
        paddingBottom: Math.round(height * margin),
      }}
    >
      <div
        style={{
          maxWidth: "80%",
          padding: "14px 28px",
          borderRadius: 12,
          backgroundColor: "rgba(12, 16, 24, 0.62)",
          color: "#ffffff",
          fontSize: 40,
          lineHeight: 1.35,
          textAlign: "center",
          fontFamily:
            '"PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", system-ui, sans-serif',
        }}
      >
        {active.text}
      </div>
    </AbsoluteFill>
  );
};
