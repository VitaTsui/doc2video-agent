import React from "react";
import { Composition } from "remotion";

import { Main } from "./compositions/Main";
import { generatedScenes, totalDuration } from "./compositions/generated-scenes";
import { toFrames, videoConfig } from "./video-config";

/**
 * The film is exactly as long as its voiced scenes.
 *
 * `totalDuration` comes from the registry, which measured it from the audio.
 * The one-frame floor is for an empty project: Remotion refuses a composition
 * of zero frames, and a fresh template has no scenes yet.
 */
export const RemotionRoot: React.FC = () => (
  <Composition
    id="Main"
    component={Main}
    durationInFrames={Math.max(1, toFrames(totalDuration))}
    fps={videoConfig.fps}
    width={videoConfig.width}
    height={videoConfig.height}
  />
);

export const sceneCount = generatedScenes.length;
