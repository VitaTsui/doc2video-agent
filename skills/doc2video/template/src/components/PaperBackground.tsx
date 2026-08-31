import React from "react";

import { palette } from "../design-system";

/**
 * The paper every scene sits on.
 *
 * A generated scene must not paint its own full-frame background — twenty
 * scenes each choosing a shade is what makes a film look stitched together.
 * The host paints this once, underneath all of them, and scenes draw on top.
 *
 * The fibre texture is procedural rather than an image file: a scene that
 * depends on `staticFile` fails differently in Studio and in a render, and a
 * background is not worth that class of bug.
 */
export const PaperBackground: React.FC = () => (
  <div
    style={{
      position: "absolute",
      inset: 0,
      backgroundColor: palette.cream,
      backgroundImage: [
        `radial-gradient(circle at 18% 22%, ${palette.creamDeep} 0%, transparent 42%)`,
        `radial-gradient(circle at 82% 78%, ${palette.creamDeep} 0%, transparent 38%)`,
        "repeating-linear-gradient(94deg, rgba(42,39,36,0.022) 0px, rgba(42,39,36,0.022) 1px, transparent 1px, transparent 3px)",
        "repeating-linear-gradient(4deg, rgba(42,39,36,0.018) 0px, rgba(42,39,36,0.018) 1px, transparent 1px, transparent 4px)",
      ].join(", "),
    }}
  />
);
