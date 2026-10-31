import { describe, expect, it } from "vitest";

import {
  BADGE_PULSE_SCALE,
  badgePulse,
  cardMount,
  chipCrossfade,
  colorTransitionStyle,
  durations,
  easeOut,
  messageEnter,
  paneOpen,
  REDUCED_FADE_MS,
  spring,
  type MotionPreset,
} from "./motion";

const PRESETS: [string, (reduced: boolean) => MotionPreset][] = [
  ["message-enter", messageEnter],
  ["chip-crossfade", chipCrossfade],
  ["card-mount", cardMount],
  ["pane-open", paneOpen],
];

function hasTransform(preset: MotionPreset): boolean {
  return "transform" in preset.initial || "transform" in preset.animate;
}

describe("motion tokens", () => {
  it("match 05-design.md", () => {
    expect(durations).toEqual({ micro: 150, enter: 180, chip: 200, pane: 220, layout: 280 });
    expect(easeOut).toEqual([0.2, 0, 0, 1]);
    expect(spring).toMatchObject({ type: "spring", stiffness: 500, damping: 40 });
    expect(REDUCED_FADE_MS).toBeLessThanOrEqual(120);
  });
});

describe("messageEnter", () => {
  it("AC-078a enter preset is 180 ± 20 ms with an 8 px translateY", () => {
    const preset = messageEnter(false);
    expect(preset.name).toBe("message-enter");
    expect(Math.abs(preset.durationMs - 180)).toBeLessThanOrEqual(20);
    expect(preset.transition.duration).toBeCloseTo(0.18);
    expect(preset.initial).toEqual({ opacity: 0, transform: "translateY(8px)" });
    expect(preset.animate).toEqual({ opacity: 1, transform: "translateY(0px)" });
  });

  it("AC-079a reduced motion: no transform, opacity only, ≤ 120 ms", () => {
    const preset = messageEnter(true);
    expect(preset.name).toBe("message-enter-reduced");
    expect(hasTransform(preset)).toBe(false);
    expect(preset.durationMs).toBeLessThanOrEqual(120);
    expect(preset.initial).toEqual({ opacity: 0 });
  });
});

describe.each(PRESETS)("%s preset", (name, make) => {
  it("FR-078 has its planned duration and name", () => {
    const preset = make(false);
    expect(preset.name).toBe(name);
    expect(preset.durationMs).toBeGreaterThan(0);
  });

  it("AC-079a reduced motion drops transforms and stays ≤ 120 ms", () => {
    const preset = make(true);
    expect(preset.name).toBe(`${name}-reduced`);
    expect(hasTransform(preset)).toBe(false);
    expect(preset.durationMs).toBeLessThanOrEqual(120);
    expect((preset.transition.duration ?? 0) * 1000).toBeLessThanOrEqual(120);
  });
});

describe("FR-078 preset values", () => {
  it("chip crossfade is 200 ms opacity only", () => {
    const preset = chipCrossfade(false);
    expect(preset.durationMs).toBe(200);
    expect(hasTransform(preset)).toBe(false);
  });

  it("card mount springs 500/40 from scale 0.97 and settles ≤ 300 ms", () => {
    const preset = cardMount(false);
    expect(preset.transition).toMatchObject({ type: "spring", stiffness: 500, damping: 40 });
    expect(preset.initial.transform).toBe("scale(0.97)");
    expect(preset.durationMs).toBeLessThanOrEqual(300);
  });

  it("pane open slides 220 ms", () => {
    const preset = paneOpen(false);
    expect(preset.durationMs).toBe(220);
    expect(preset.initial.transform).toBe("translateX(16px)");
  });

  it("badge pulse is 1 → 1.08 → 1 over 150 ms; none under reduced motion", () => {
    const pulse = badgePulse(false);
    expect(pulse?.keyframes.transform).toEqual([
      "scale(1)",
      `scale(${BADGE_PULSE_SCALE})`,
      "scale(1)",
    ]);
    expect(BADGE_PULSE_SCALE).toBe(1.08);
    expect(pulse?.transition.duration).toBeCloseTo(0.15);
    expect(badgePulse(true)).toBeNull();
  });

  it("colour transitions list properties (never all) and cap at 120 ms reduced", () => {
    const normal = colorTransitionStyle(false);
    expect(normal.transitionProperty).not.toMatch(/\ball\b/);
    expect(normal.transitionDuration).toBe("200ms");
    expect(colorTransitionStyle(true).transitionDuration).toBe("120ms");
  });
});
