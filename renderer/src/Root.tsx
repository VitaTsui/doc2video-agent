import React from "react";
import { Composition } from "remotion";
import { SceneComposition } from "./compositions/Scene";
import { SlidesComposition } from "./slides/Slides";
import type { SlideDeck } from "./slides/types";
import type { RawScenePlan } from "./types";

const DEFAULT_PLAN: RawScenePlan = {
  scene_id: "scene_01",
  duration: 6,
  width: 1920,
  height: 1080,
  fps: 30,
  image: null,
  video: null,
  audio: null,
  actions: [],
  subtitles: [],
  transition_in: "fade",
  transition_duration: 0.4,
};

const DEFAULT_DECK: SlideDeck = {
  width: 1920,
  height: 1080,
  pt_to_px: 2.667,
  slides: [],
};

export const RemotionRoot: React.FC = () => (
  <>
    {/* One composition, one scene. Rendering per scene is what makes
        scene-level incremental re-render possible: changing page 7
        re-renders one clip. */}
    <Composition
      id="Scene"
      component={SceneComposition}
      defaultProps={DEFAULT_PLAN}
      durationInFrames={180}
      fps={30}
      width={1920}
      height={1080}
      calculateMetadata={({ props }) => ({
        // Everything comes from the plan, so the CLI never needs render flags.
        durationInFrames: Math.max(1, Math.round(props.duration * props.fps)),
        fps: props.fps,
        width: props.width,
        height: props.height,
      })}
    />

    {/* Slide rasterizer: one frame per slide, rendered as an image sequence.
        This is the LibreOffice-free path to high-fidelity page images. */}
    <Composition
      id="Slides"
      component={SlidesComposition}
      defaultProps={DEFAULT_DECK}
      durationInFrames={1}
      fps={1}
      width={1920}
      height={1080}
      calculateMetadata={({ props }) => ({
        durationInFrames: Math.max(1, props.slides.length),
        fps: 1,
        width: props.width,
        height: props.height,
      })}
    />
  </>
);
