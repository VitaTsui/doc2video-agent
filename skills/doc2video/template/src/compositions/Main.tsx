import React from "react";
import { AbsoluteFill, Audio, Sequence, staticFile } from "remotion";

import { BrollScene } from "../components/BrollScene";
import { PaperBackground } from "../components/PaperBackground";
import { Subtitles } from "../components/Subtitles";
import { palette, typography } from "../design-system";
import { stageScale, toFrames, videoConfig } from "../video-config";
import {
  generatedScenes,
  type GeneratedSceneItem,
} from "./generated-scenes";

/**
 * The host. Lays the voiced timeline out and hands each slot to its scene.
 *
 * Every scene's audio rides in its own Sequence rather than in one global
 * track. That is what keeps "change one scene" cheap: re-voicing scene 7
 * rewrites one wav and one registry row, and scenes 1..6 and 8..n are
 * untouched. A single stitched track would have to be rebuilt every time.
 */

const sceneBody = (scene: GeneratedSceneItem, slotDurationInFrames: number) => {
  if (scene.rollType === "b-roll") {
    return (
      <BrollScene
        videoSrc={scene.videoSrc}
        mediaDuration={scene.mediaDuration}
        slotDurationInFrames={slotDurationInFrames}
      />
    );
  }
  const Component = scene.Component;
  return (
    <Component segments={scene.segments} durationInFrames={slotDurationInFrames} />
  );
};

export const Main: React.FC = () => (
  <AbsoluteFill style={{ backgroundColor: palette.cream, overflow: "hidden" }}>
    <div
      style={{
        position: "absolute",
        width: videoConfig.designWidth,
        height: videoConfig.designHeight,
        transform: `scale(${stageScale})`,
        transformOrigin: "top left",
        overflow: "hidden",
        fontFamily: typography.sans,
        color: palette.charcoal,
      }}
    >
      <PaperBackground />

      {generatedScenes.map((scene, index) => {
        const from = toFrames(scene.start);
        const next = generatedScenes[index + 1];
        // The picture holds through the gap; only the voice stops.
        //
        // Scenes are laid out with a breath between them so the last word of
        // one does not collide with the first of the next. Ending the picture
        // with the audio left that breath empty — a third of a second of bare
        // paper between every pair of scenes, which reads as a flicker rather
        // than as a pause. So the visual slot runs to wherever the next scene
        // starts; the audio inside it is its own length and simply finishes
        // early.
        const until = next ? toFrames(next.start) : toFrames(scene.start + scene.duration);
        const slot = Math.max(1, until - from);

        return (
          <Sequence
            key={scene.id}
            name={scene.id}
            from={from}
            durationInFrames={slot}
            premountFor={videoConfig.fps}
          >
            {sceneBody(scene, slot)}
            <Audio src={staticFile(scene.audioSrc)} />
            <Subtitles segments={scene.segments} />
          </Sequence>
        );
      })}
    </div>
  </AbsoluteFill>
);
