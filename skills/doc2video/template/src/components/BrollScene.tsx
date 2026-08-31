import React from "react";
import { AbsoluteFill, OffthreadVideo, staticFile } from "remotion";

import { videoConfig } from "../video-config";

/**
 * A B-roll slot: generated footage filling the frame for as long as its
 * narration lasts.
 *
 * Two invariants, both learned the expensive way in the pipeline this borrows
 * from:
 *
 * **The clip is always silent.** Generated video often carries an audio track
 * of its own — room tone, a synthetic whoosh — and the film's sound is the
 * narration and nothing else. One clip that forgets this is a scene where the
 * voice fights a noise nobody asked for.
 *
 * **The slot length wins, not the clip length.** The timeline is the voiced
 * one. A clip shorter than its slot holds its last frame; a longer one is cut
 * off. Neither case may change when the next scene starts.
 */
export const BrollScene: React.FC<{
  videoSrc: string;
  mediaDuration: number;
  slotDurationInFrames: number;
}> = ({ videoSrc, mediaDuration, slotDurationInFrames }) => {
  const slotSeconds = slotDurationInFrames / videoConfig.fps;
  // Slow the clip down rather than freeze on black when it runs short. Below
  // this the footage would crawl, and a held last frame reads better than
  // slow motion.
  const stretch = mediaDuration > 0 ? slotSeconds / mediaDuration : 1;
  const playbackRate = stretch > 1 ? Math.max(0.5, 1 / Math.min(stretch, 2)) : 1;

  return (
    <AbsoluteFill style={{ backgroundColor: "#000", overflow: "hidden" }}>
      <OffthreadVideo
        src={staticFile(videoSrc)}
        muted
        playbackRate={playbackRate}
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
    </AbsoluteFill>
  );
};
