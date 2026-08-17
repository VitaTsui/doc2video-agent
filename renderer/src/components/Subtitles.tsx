import React from "react";
import { AbsoluteFill } from "remotion";
import type { PlanSubtitle } from "../types";

/**
 * Subtitles render outside the camera layer so they stay legible and fixed
 * while the page zooms. One cue at a time — overlapping cues would stack.
 */
export const Subtitles: React.FC<{ cues: PlanSubtitle[]; time: number }> = ({
  cues,
  time,
}) => {
  const active = cues.find((cue) => time >= cue.start && time < cue.end);
  if (!active) {
    return null;
  }

  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-end",
        alignItems: "center",
        paddingBottom: "6%",
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
