import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { Shape } from "./Shape";
import type { SlideDeck } from "./types";

/**
 * A whole deck rendered as an image sequence: **frame index = slide index**.
 *
 * One `remotion render --sequence` produces every page from a single bundle,
 * instead of paying bundle + browser startup once per slide.
 */
export const SlidesComposition: React.FC<SlideDeck> = (deck) => {
  const frame = useCurrentFrame();
  const slide = deck.slides[Math.min(frame, deck.slides.length - 1)];

  if (!slide) {
    return <AbsoluteFill style={{ backgroundColor: "#FFFFFF" }} />;
  }

  return (
    <AbsoluteFill style={{ backgroundColor: slide.background }}>
      {/* Shapes are drawn in document order, which is PowerPoint's z-order. */}
      {slide.shapes.map((shape, index) => (
        <Shape key={index} shape={shape} ptToPx={deck.pt_to_px} />
      ))}
    </AbsoluteFill>
  );
};
