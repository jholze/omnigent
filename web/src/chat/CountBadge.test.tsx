import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type * as MotionReact from "motion/react";

import { CountBadge, countBadgeText } from "./CountBadge";
import type * as MotionRootModule from "./MotionRoot";

const reducedMotion = vi.hoisted(() => ({ value: false }));
const animateSpy = vi.hoisted(() => vi.fn());

vi.mock("./MotionRoot", async (importOriginal) => ({
  ...(await importOriginal<typeof MotionRootModule>()),
  useChatReducedMotion: () => reducedMotion.value,
}));
vi.mock("motion/react", async (importOriginal) => {
  const actual = await importOriginal<typeof MotionReact>();
  return {
    ...actual,
    useAnimate: () => {
      const [scope] = actual.useAnimate();
      return [scope, animateSpy];
    },
  };
});

beforeEach(() => {
  reducedMotion.value = false;
  animateSpy.mockReset();
});
afterEach(() => cleanup());

describe("CountBadge", () => {
  it("FR-078 badge pulses 1 → 1.08 → 1 over 150 ms when the count changes", () => {
    const { rerender } = render(<CountBadge count={2} label="2 ungelesen" />);
    expect(animateSpy).not.toHaveBeenCalled();
    rerender(<CountBadge count={3} label="3 ungelesen" />);
    expect(animateSpy).toHaveBeenCalledTimes(1);
    const [el, keyframes, transition] = animateSpy.mock.calls[0];
    expect(el).toBe(screen.getByLabelText("3 ungelesen"));
    expect(keyframes).toEqual({ transform: ["scale(1)", "scale(1.08)", "scale(1)"] });
    expect(transition.duration).toBeCloseTo(0.15);
    expect(screen.getByLabelText("3 ungelesen").dataset.motion).toBe("badge-pulse");
  });

  it("AC-079a reduced motion: no badge pulse", () => {
    reducedMotion.value = true;
    const { rerender } = render(<CountBadge count={2} label="2 ungelesen" />);
    rerender(<CountBadge count={5} label="5 ungelesen" />);
    expect(animateSpy).not.toHaveBeenCalled();
    expect(screen.getByLabelText("5 ungelesen").dataset.motion).toBe("badge-pulse-reduced");
  });

  it("hides at 0 and caps the text at 99+", () => {
    const { container } = render(<CountBadge count={0} label="0" />);
    expect(container.firstChild).toBeNull();
    expect(countBadgeText(100)).toBe("99+");
    expect(countBadgeText(7)).toBe("7");
  });
});
