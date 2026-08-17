import React from "react";
import { interpolate } from "remotion";
import type { PlanAction } from "../types";

const ACCENT = "#F2B705";
const FADE = 0.25;

/** An outline drawn around an element, fading in and out with its action. */
export const Highlight: React.FC<{ action: PlanAction; time: number }> = ({
  action,
  time,
}) => {
  const area = action.area;
  if (!area || time < action.start - FADE || time > action.end + FADE) {
    return null;
  }

  const opacity = interpolate(
    time,
    [action.start - FADE, action.start, action.end, action.end + FADE],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  return (
    <div
      style={{
        position: "absolute",
        left: `${area.x * 100}%`,
        top: `${area.y * 100}%`,
        width: `${area.w * 100}%`,
        height: `${area.h * 100}%`,
        border: `4px solid ${ACCENT}`,
        borderRadius: 10,
        boxShadow: `0 0 0 9999px rgba(12, 16, 24, ${0.28 * opacity})`,
        opacity,
      }}
    />
  );
};
