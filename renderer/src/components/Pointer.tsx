import React from "react";
import { interpolate, spring, useVideoConfig } from "remotion";
import type { PlanAction } from "../types";

const POINTER_COLOR = "#E2574C";

/** A marker that lands on the target and pulses once — "look here". */
export const Pointer: React.FC<{ action: PlanAction; time: number }> = ({
  action,
  time,
}) => {
  const { fps } = useVideoConfig();
  const area = action.area;
  if (!area || time < action.start || time > action.end) {
    return null;
  }

  const localFrame = Math.round((time - action.start) * fps);
  const pop = spring({ frame: localFrame, fps, config: { damping: 14 } });
  const opacity = interpolate(
    time,
    [action.start, action.start + 0.15, action.end - 0.2, action.end],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  return (
    <div
      style={{
        position: "absolute",
        left: `${(area.x + area.w / 2) * 100}%`,
        top: `${(area.y + area.h / 2) * 100}%`,
        width: 28,
        height: 28,
        marginLeft: -14,
        marginTop: -14,
        borderRadius: "50%",
        backgroundColor: POINTER_COLOR,
        boxShadow: `0 0 0 ${8 * pop}px rgba(226, 87, 76, 0.25)`,
        transform: `scale(${0.6 + 0.4 * pop})`,
        opacity,
      }}
    />
  );
};
