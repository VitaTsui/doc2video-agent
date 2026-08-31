/**
 * The look, in one file, so twenty generated scenes look like one film.
 *
 * Every scene imports from here and nothing else invents a colour. That is the
 * whole point: the scenes are written by a model, one batch at a time, and
 * without a shared palette each batch drifts — five greens, three type scales,
 * and a video that looks assembled rather than made.
 */

export const palette = {
  /** Paper. Everything sits on this. */
  cream: "#F4EFE6",
  creamDeep: "#E8E0D2",
  /** Ink. Body text and outlines. */
  charcoal: "#2A2724",
  charcoalSoft: "#5A544C",
  /** The one accent that carries meaning — highlights, active states, key numbers. */
  accent: "#C8553D",
  accentSoft: "#E2A28F",
  /** Support colours for charts and grouping. Use in this order. */
  series: ["#C8553D", "#3D6B7D", "#D4A24C", "#6B7D3D", "#7D5A8C", "#4C7D75"],
  /** Paper shadow. Never pure black. */
  shadow: "rgba(42, 39, 36, 0.18)",
} as const;

export const typography = {
  /** Bundled with the skill; the sandbox usually ships no CJK face at all. */
  sans: '"Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
  /** Design-stage pixels. The stage is 1920x1080 and scales as a whole. */
  scale: {
    display: 96,
    title: 64,
    heading: 44,
    body: 32,
    caption: 24,
  },
  weight: {
    regular: 400,
    medium: 500,
    bold: 700,
  },
  lineHeight: {
    tight: 1.15,
    normal: 1.45,
  },
} as const;

export const layout = {
  /** Nothing important goes outside this margin — it is the title-safe area. */
  margin: 120,
  gap: 32,
  radius: 18,
  /** A card of content on the paper. */
  cardPadding: 40,
} as const;

/**
 * Motion, in frames at 30fps.
 *
 * Kept short on purpose. A scene lasts as long as its narration and no longer,
 * so an animation that takes two seconds to settle has eaten a third of a
 * short scene before the viewer has read anything.
 */
export const motion = {
  /** One element arriving. */
  enter: 12,
  /** The stagger between siblings, so a list arrives as a list. */
  stagger: 4,
  /** Leaving. Faster than arriving; nobody watches an exit. */
  exit: 8,
  /** Remotion spring config for anything that should feel like paper, not rubber. */
  spring: { damping: 200, stiffness: 100, mass: 0.6 },
} as const;

export const designTokens = { palette, typography, layout, motion } as const;
