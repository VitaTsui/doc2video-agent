/**
 * Renderer-facing DSL. Mirrors `doc2video/tools/renderer/base.py` — the Agent
 * emits this shape and no renderer is allowed to require anything more.
 */

export type ActionType =
  | "zoom"
  | "highlight"
  | "pointer"
  | "pan"
  | "reset"
  | "transition"
  | "broll";

/** Target region, normalized to 0..1 of the frame. */
export type PlanArea = {
  x: number;
  y: number;
  w: number;
  h: number;
};

export type PlanAction = {
  type: ActionType;
  /** Seconds, relative to the start of this scene. */
  start: number;
  end: number;
  effect: string;
  target: string | null;
  area: PlanArea | null;
  params: Record<string, unknown>;
};

export type PlanSubtitle = {
  start: number;
  end: number;
  text: string;
};

export type ScenePlan = {
  sceneId: string;
  duration: number;
  width: number;
  height: number;
  fps: number;
  /** Path relative to `public/`, consumed via `staticFile()`. */
  image: string | null;
  video: string | null;
  audio: string | null;
  actions: PlanAction[];
  subtitles: PlanSubtitle[];
  transitionIn: string;
  transitionDuration: number;
};

/** Python emits snake_case; this is what actually arrives in props. */
export type RawScenePlan = {
  scene_id: string;
  duration: number;
  width: number;
  height: number;
  fps: number;
  image: string | null;
  video: string | null;
  audio: string | null;
  actions: PlanAction[];
  subtitles: PlanSubtitle[];
  transition_in: string;
  transition_duration: number;
};

export const normalizePlan = (raw: RawScenePlan): ScenePlan => ({
  sceneId: raw.scene_id,
  duration: raw.duration,
  width: raw.width,
  height: raw.height,
  fps: raw.fps,
  image: raw.image,
  video: raw.video,
  audio: raw.audio,
  actions: raw.actions ?? [],
  subtitles: raw.subtitles ?? [],
  transitionIn: raw.transition_in ?? "fade",
  transitionDuration: raw.transition_duration ?? 0.4,
});
