import React from "react";
import { interpolate, spring, useVideoConfig } from "remotion";
import type { PlanAction } from "../types";

const POINTER_COLOR = "#E2574C";

/**
 * A marker that lands beside the target and pulses once — "look here".
 *
 * Beside, not on. Centred, it sat on top of the words it was pointing at: a
 * red disc over 「核心市场痛点分析」 covers two of the six characters it exists
 * to draw the eye to. It goes to the left edge instead, just outside the box,
 * and falls back to inside that edge when the target is already against the
 * left of the frame.
 */
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

  // A little to the left of the target, vertically centred on it. `GAP` is in
  // fractions of the frame, so it holds at any render size.
  const GAP = 0.012;
  const left = area.x - GAP > 0.01 ? area.x - GAP : area.x + Math.min(area.w / 2, GAP);

  return (
    <div
      style={{
        position: "absolute",
        left: `${left * 100}%`,
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
