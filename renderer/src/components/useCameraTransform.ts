import type { PlanAction } from "../types";

export type CameraTransform = {
  scale: number;
  /** Percent of the element's own size — see the derivation below. */
  translateX: number;
  translateY: number;
};

const IDENTITY: CameraTransform = { scale: 1, translateX: 0, translateY: 0 };

/** Never zoom past this: a tiny target would turn into a blur. */
const MAX_SCALE = 3;
const MIN_TARGET_SIDE = 0.05;
/** Share of the action spent easing in, and again easing out. */
const EASE_SHARE = 0.3;
const MAX_EASE_SECONDS = 0.6;

const easeInOut = (t: number): number =>
  t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;

/**
 * Camera state at a point in time.
 *
 * The transform is `scale(s) translate(tx%, ty%)` with a centred origin, so
 * translate is applied first in local space: a point p lands at
 * `0.5 + s * ((p + t) - 0.5)`. Centring the target means `t = 0.5 - center`,
 * independent of `s` — which is why the pan and the zoom can be eased with a
 * single progress value.
 */
export const useCameraTransform = (
  actions: PlanAction[],
  time: number,
): CameraTransform => {
  const zooms = actions.filter(
    (action) => (action.type === "zoom" || action.type === "pan") && action.area,
  );

  for (const action of zooms) {
    if (time < action.start || time > action.end) {
      continue;
    }
    const area = action.area!;
    const span = Math.max(action.end - action.start, 0.001);
    const ease = Math.min(MAX_EASE_SECONDS, span * EASE_SHARE);

    let progress = 1;
    if (ease > 0) {
      if (time < action.start + ease) {
        progress = easeInOut((time - action.start) / ease);
      } else if (time > action.end - ease) {
        progress = easeInOut(Math.max(0, action.end - time) / ease);
      }
    }

    const targetScale =
      action.type === "pan"
        ? 1
        : Math.min(
            MAX_SCALE,
            Math.max(1.05, 1 / Math.max(area.w, area.h, MIN_TARGET_SIDE)),
          );
    const centerX = area.x + area.w / 2;
    const centerY = area.y + area.h / 2;

    return {
      scale: 1 + (targetScale - 1) * progress,
      translateX: (0.5 - centerX) * 100 * progress,
      translateY: (0.5 - centerY) * 100 * progress,
    };
  }

  return IDENTITY;
};
