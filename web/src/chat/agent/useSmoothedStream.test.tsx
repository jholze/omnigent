// Smoothed live agent text (FR-080): one paint per frame while streaming,
// immediate paint on stream end, shrink, reset and reduced motion.
// Animation frames run from a manual queue so each test decides when one fires.
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type * as MotionRootModule from "../MotionRoot";
import { useSmoothedStream } from "./useSmoothedStream";

const reducedMotion = vi.hoisted(() => ({ value: false }));

vi.mock("../MotionRoot", async (importOriginal) => ({
  ...(await importOriginal<typeof MotionRootModule>()),
  useChatReducedMotion: () => reducedMotion.value,
}));

let frames: Map<number, FrameRequestCallback>;
let nextFrameId: number;

function runFrame() {
  const due = [...frames.values()];
  frames.clear();
  act(() => {
    for (const callback of due) callback(performance.now());
  });
}

function renderStream(target: string, active: boolean) {
  return renderHook(
    ({ text, streaming }: { text: string; streaming: boolean }) =>
      useSmoothedStream(text, streaming),
    { initialProps: { text: target, streaming: active } },
  );
}

beforeEach(() => {
  reducedMotion.value = false;
  frames = new Map();
  nextFrameId = 1;
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    const id = nextFrameId++;
    frames.set(id, callback);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => {
    frames.delete(id);
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("useSmoothedStream", () => {
  it("AC-080a coalesces several updates in one turn into one frame with the full text", () => {
    const hook = renderStream("", true);
    hook.rerender({ text: "Tests ", streaming: true });
    hook.rerender({ text: "Tests sind ", streaming: true });
    hook.rerender({ text: "Tests sind grün", streaming: true });

    expect(hook.result.current).toBe("");
    expect(frames.size).toBe(1);

    runFrame();

    expect(hook.result.current).toBe("Tests sind grün");
    expect(frames.size).toBe(0);
  });

  it("AC-080a snaps to the target immediately when the stream ends", () => {
    const hook = renderStream("", true);
    hook.rerender({ text: "Tests sind", streaming: true });
    expect(hook.result.current).toBe("");

    hook.rerender({ text: "Tests sind grün", streaming: false });

    expect(hook.result.current).toBe("Tests sind grün");
  });

  it("AC-080a snaps to the target immediately when it shrinks or is replaced", () => {
    const hook = renderStream("", true);
    hook.rerender({ text: "Tests sind grün", streaming: true });
    runFrame();
    expect(hook.result.current).toBe("Tests sind grün");

    hook.rerender({ text: "Tests", streaming: true });
    expect(hook.result.current).toBe("Tests");

    hook.rerender({ text: "Neuer Turn", streaming: true });
    expect(hook.result.current).toBe("Neuer Turn");
  });

  it("AC-080a paints immediately with reduced motion", () => {
    reducedMotion.value = true;
    const hook = renderStream("", true);

    hook.rerender({ text: "Tests ", streaming: true });
    expect(hook.result.current).toBe("Tests ");
    hook.rerender({ text: "Tests sind grün", streaming: true });
    expect(hook.result.current).toBe("Tests sind grün");
    expect(frames.size).toBe(0);
  });
});
