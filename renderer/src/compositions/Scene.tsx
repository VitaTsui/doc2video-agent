import React from "react";
import {
  AbsoluteFill,
  Img,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { Highlight } from "../components/Highlight";
import { Pointer } from "../components/Pointer";
import { LiveChart } from "../components/LiveChart";
import { Subtitles } from "../components/Subtitles";
import { useCameraTransform } from "../components/useCameraTransform";
import { normalizePlan, type RawScenePlan } from "../types";

/**
 * Renders one scene. The plan is authoritative: this component decides *how*
 * to realise zoom / highlight / pointer, never *whether* or *when* — that was
 * decided by the director skill against the audio timeline.
 */
export const SceneComposition: React.FC<RawScenePlan> = (raw) => {
  const plan = normalizePlan(raw);
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const time = frame / fps;

  const camera = useCameraTransform(plan.actions, time);

  const fadeIn =
    plan.transitionDuration > 0
      ? interpolate(time, [0, plan.transitionDuration], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        })
      : 1;

  return (
    <AbsoluteFill style={{ backgroundColor: "#ffffff" }}>
      <AbsoluteFill style={{ opacity: fadeIn }}>
        {/* Camera layer: zoom and pan apply to the page and every overlay on
            it together, so a highlight stays glued to its element. */}
        <AbsoluteFill
          style={{
            transform: `scale(${camera.scale}) translate(${camera.translateX}%, ${camera.translateY}%)`,
            transformOrigin: "center center",
          }}
        >
          {plan.image ? (
            <Img
              src={staticFile(plan.image)}
              style={{ width: "100%", height: "100%", objectFit: "contain" }}
            />
          ) : null}

          {/* Before the overlays: a chart is redrawn over the page, and the
              outline the director put around it has to stay on top of the
              redraw or pointing at the chart stops being visible while the
              chart is being drawn. */}
          {plan.charts.map((chart, index) => (
            <LiveChart
              key={`ch-${index}`}
              chart={chart}
              time={time}
              frameWidth={plan.width}
              frameHeight={plan.height}
            />
          ))}

          {plan.actions
            .filter((action) => action.type === "highlight" && action.area)
            .map((action, index) => (
              <Highlight key={`hl-${index}`} action={action} time={time} />
            ))}

          {plan.actions
            .filter((action) => action.type === "pointer" && action.area)
            .map((action, index) => (
              <Pointer key={`pt-${index}`} action={action} time={time} />
            ))}

        </AbsoluteFill>
      </AbsoluteFill>

      {/* Subtitles sit outside the camera layer — they must not zoom. */}
      <Subtitles
        cues={plan.subtitles}
        time={time}
        margin={plan.subtitleMargin}
        height={plan.height}
      />
    </AbsoluteFill>
  );
};
