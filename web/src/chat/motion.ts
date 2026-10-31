/**
 * Motion tokens and presets for the team chat (FR-078, FR-079, NFR-007,
 * NFR-008; spec 05-design.md "Motion-Tokens", ADR-0008).
 *
 * Every duration, curve and spring in `web/src/chat/` comes from here. No
 * other values. Reduced motion (`prefers-reduced-motion: reduce`) drops all
 * transforms and keeps at most an opacity change of `REDUCED_FADE_MS`.
 *
 * Presets animate `transform` strings and `opacity` only, so Motion can hand
 * them to the compositor (WAAPI) and `element.getAnimations()` sees them.
 */
import type { Transition } from "motion/react";

/** Durations in ms (05-design.md). */
export const durations = {
  micro: 150,
  enter: 180,
  chip: 200,
  pane: 220,
  layout: 280,
} as const;

/** Upper bound for any animation while reduced motion is on (FR-079, NFR-008). */
export const REDUCED_FADE_MS = 120;

export const easeOut = [0.2, 0, 0, 1] as const;
export const easeOutCss = `cubic-bezier(${easeOut.join(", ")})`;

export const spring = { type: "spring", stiffness: 500, damping: 40 } as const;
export const pop = { type: "spring", stiffness: 640, damping: 22, mass: 0.7 } as const;
export const cell = { type: "spring", stiffness: 520, damping: 34, mass: 0.45 } as const;
export const spinSeconds = 0.8;
export const shimmerSeconds = 1.6;

/** Distances in px. `enterY` is FR-078; `paneX` is the thread pane slide offset. */
export const distances = { enterY: 8, paneX: 16 } as const;

/** Badge pulse peak (1 → 1.08 → 1). */
export const BADGE_PULSE_SCALE = 1.08;

const seconds = (ms: number) => ms / 1000;

export interface MotionPreset {
  initial: Record<string, string | number>;
  animate: Record<string, string | number>;
  transition: Transition;
  /** `data-motion` value; E2E and Vitest read it. */
  name: string;
  /** Planned duration in ms (springs: settle bound). */
  durationMs: number;
}

/** Opacity-only fade used for every preset under reduced motion. */
function reducedFade(name: string): MotionPreset {
  return {
    initial: { opacity: 0 },
    animate: { opacity: 1 },
    transition: { duration: seconds(REDUCED_FADE_MS), ease: easeOut },
    name: `${name}-reduced`,
    durationMs: REDUCED_FADE_MS,
  };
}

/** New message from someone else: opacity 0→1, y 8→0 px, 180 ms. */
export function messageEnter(reduced: boolean): MotionPreset {
  if (reduced) return reducedFade("message-enter");
  return {
    initial: { opacity: 0, transform: `translateY(${distances.enterY}px)` },
    animate: { opacity: 1, transform: "translateY(0px)" },
    transition: { duration: seconds(durations.enter), ease: easeOut },
    name: "message-enter",
    durationMs: durations.enter,
  };
}

/** Status chip label change: fade the new label in over 200 ms (no transform). */
export function chipCrossfade(reduced: boolean): MotionPreset {
  if (reduced) return reducedFade("chip-crossfade");
  return {
    initial: { opacity: 0 },
    animate: { opacity: 1 },
    transition: { duration: seconds(durations.chip), ease: easeOut },
    name: "chip-crossfade",
    durationMs: durations.chip,
  };
}

/** Approval card mount: spring 500/40 from scale 0.97, settles in < 300 ms. */
export function cardMount(reduced: boolean): MotionPreset {
  if (reduced) return reducedFade("card-mount");
  return {
    initial: { opacity: 0, transform: "scale(0.97)" },
    animate: { opacity: 1, transform: "scale(1)" },
    transition: { ...spring, opacity: { duration: seconds(durations.micro), ease: easeOut } },
    name: "card-mount",
    durationMs: durations.layout,
  };
}

/** Thread pane open: slide in from its edge (right), 220 ms. */
export function paneOpen(reduced: boolean): MotionPreset {
  if (reduced) return reducedFade("pane-open");
  return {
    initial: { opacity: 0, transform: `translateX(${distances.paneX}px)` },
    animate: { opacity: 1, transform: "translateX(0px)" },
    transition: { duration: seconds(durations.pane), ease: easeOut },
    name: "pane-open",
    durationMs: durations.pane,
  };
}

/**
 * Unread badge change: scale pulse 1 → 1.08 → 1 over 150 ms. Under reduced
 * motion there is no pulse at all (`null`).
 */
export function badgePulse(reduced: boolean): {
  keyframes: { transform: string[] };
  transition: Transition;
} | null {
  if (reduced) return null;
  return {
    keyframes: { transform: ["scale(1)", `scale(${BADGE_PULSE_SCALE})`, "scale(1)"] },
    transition: { duration: seconds(durations.micro), ease: easeOut },
  };
}

/**
 * Inline style for CSS colour transitions (chip tone). Lists properties,
 * never `all`; reduced motion caps the duration at 120 ms.
 */
export function colorTransitionStyle(reduced: boolean): {
  transitionProperty: string;
  transitionDuration: string;
  transitionTimingFunction: string;
} {
  return {
    transitionProperty: "color, background-color, border-color",
    transitionDuration: `${reduced ? REDUCED_FADE_MS : durations.chip}ms`,
    transitionTimingFunction: easeOutCss,
  };
}
