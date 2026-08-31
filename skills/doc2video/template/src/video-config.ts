/**
 * One place that knows the frame.
 *
 * Scenes are written against a fixed 1920x1080 design stage and scaled to
 * whatever the output is, so a generated scene never has to reason about the
 * render resolution — it lays out in design pixels and the host scales it. A
 * scene that hard-codes output pixels breaks the day someone renders 4K.
 */
export const videoConfig = {
  fps: 30,
  width: 1920,
  height: 1080,
  designWidth: 1920,
  designHeight: 1080,
} as const;

export const stageScale = videoConfig.width / videoConfig.designWidth;

/** Seconds to frames, rounded the way every scene boundary must round. */
export const toFrames = (seconds: number): number =>
  Math.round(seconds * videoConfig.fps);
