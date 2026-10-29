// Agent step list (FR-089, FR-090): status logic, glyphs per status, the
// error rule, and reduced motion. Reduced motion is read through the mocked
// `useChatReducedMotion`, so each test picks it explicitly.
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { REDUCED_FADE_MS, spinSeconds } from "../motion";
import { MotionRoot } from "../MotionRoot";
import type * as MotionRootModule from "../MotionRoot";
import { CHAT_TEST_IDS } from "../testids";
import { AgentStatusChip } from "./AgentStatusChip";
import {
  RUN_DONE_TEXT,
  STEP_TEST_IDS,
  TaskSteps,
  progressText,
  resolveSteps,
  runFailedText,
  taskStepsState,
  type StepInput,
} from "./TaskSteps";

const reducedMotion = vi.hoisted(() => ({ value: false }));
vi.mock("../MotionRoot", async (importOriginal) => ({
  ...(await importOriginal<typeof MotionRootModule>()),
  useChatReducedMotion: () => reducedMotion.value,
}));

const TODO_LABELS = ["Tests lesen", "Fix schreiben", "Tests ausführen", "PR öffnen"] as const;

/** The four todos with step 3 in the given status; done steps took 3.4 s and 1.2 s. */
function fourSteps(third: StepInput["status"], fourth: StepInput["status"] = "pending") {
  return [
    { id: "s1", label: TODO_LABELS[0], status: "done", durationS: 3.4 },
    { id: "s2", label: TODO_LABELS[1], status: "done", durationS: 1.2 },
    { id: "s3", label: TODO_LABELS[2], status: third, durationS: 2.0 },
    { id: "s4", label: TODO_LABELS[3], status: fourth, durationS: 0.9 },
  ] satisfies StepInput[];
}

function renderSteps(input: StepInput[]) {
  const view = render(
    <MotionRoot>
      <TaskSteps steps={resolveSteps(input)} />
    </MotionRoot>,
  );
  return {
    ...view,
    rerenderSteps: (next: StepInput[]) =>
      view.rerender(
        <MotionRoot>
          <TaskSteps steps={resolveSteps(next)} />
        </MotionRoot>,
      ),
  };
}

function row(label: string): HTMLElement {
  const match = screen
    .getAllByTestId(STEP_TEST_IDS.row)
    .find((element) => element.textContent?.includes(label));
  if (match === undefined) throw new Error(`no step row for ${label}`);
  return match;
}

function iconMotion(label: string): HTMLElement {
  const status = within(row(label)).getByTestId(STEP_TEST_IDS.status);
  return status.firstElementChild as HTMLElement;
}

beforeEach(() => {
  reducedMotion.value = false;
});
afterEach(() => cleanup());

describe("useTaskSteps status logic", () => {
  it("AC-048b the active step is current and the announcement counts it", () => {
    const state = taskStepsState(resolveSteps(fourSteps("active")), false);
    expect(state.steps.map((step) => step.status)).toEqual(["done", "done", "active", "pending"]);
    expect(state.currentIndex).toBe(2);
    expect(state.complete).toBe(false);
    expect(state.announcement).toBe("Tests ausführen, Schritt 3 von 4");
    expect(progressText("Tests ausführen", 2, 4)).toBe(state.announcement);
  });

  it("AC-090a an all-done list is complete only once the run settled", () => {
    const allDone = resolveSteps(fourSteps("done", "done"));
    expect(taskStepsState(allDone, false).complete).toBe(false);
    expect(taskStepsState(allDone, false).announcement).toBeNull();
    const settled = taskStepsState(allDone, true);
    expect(settled.complete).toBe(true);
    expect(settled.announcement).toBe(RUN_DONE_TEXT);
    expect(RUN_DONE_TEXT).toBe("Lauf abgeschlossen");
  });

  it("AC-089a after an error every later step is pending with no duration", () => {
    const steps = resolveSteps(fourSteps("error", "done"));
    expect(steps.map((step) => step.status)).toEqual(["done", "done", "error", "pending"]);
    expect(steps[2].durationS).toBeNull();
    expect(steps[3].durationS).toBeNull();
    const state = taskStepsState(steps, true);
    expect(state.failedStep?.label).toBe("Tests ausführen");
    expect(state.currentIndex).toBe(2);
    expect(state.complete).toBe(false);
    expect(state.announcement).toBe(runFailedText("Tests ausführen"));
    expect(state.announcement).toBe("Lauf fehlgeschlagen bei Tests ausführen");
  });
});

describe("TaskSteps rows", () => {
  it("AC-048b done rows show a check and a mono duration, the active row a spinning arc with shimmer, pending a dot", () => {
    renderSteps(fourSteps("active"));
    const rows = screen.getAllByTestId(STEP_TEST_IDS.row);
    expect(rows.map((element) => element.dataset.status)).toEqual([
      "done",
      "done",
      "active",
      "pending",
    ]);

    for (const [label, duration] of [
      ["Tests lesen", "3.4s"],
      ["Fix schreiben", "1.2s"],
    ] as const) {
      const done = row(label);
      expect(done.querySelector("svg.lucide-check")).not.toBeNull();
      const meta = within(done).getByText(duration);
      expect(meta).toHaveClass("font-mono");
    }

    const active = row("Tests ausführen");
    const arc = active.querySelector("svg.lucide-loader-circle") as SVGElement | null;
    expect(arc).not.toBeNull();
    expect(arc).toHaveAttribute("data-spinning", "true");
    expect(arc).toHaveClass("animate-spin");
    expect(arc?.style.animationDuration).toBe(`${spinSeconds}s`);
    expect(active.querySelector(".text-shimmer")?.textContent).toBe("Tests ausführen");
    expect(active).toHaveAttribute("aria-current", "step");
    expect(within(active).queryByText(/^\d+(\.\d)?(ms|s)$/)).toBeNull();

    const pending = row("PR öffnen");
    expect(pending.querySelector("svg")).toBeNull();
    expect(pending.querySelector(".rounded-full")).not.toBeNull();
    expect(pending).not.toHaveAttribute("aria-current");
  });

  it("AC-089a a failed Tests ausführen shows a red cross and PR öffnen stays pending without duration", () => {
    renderSteps(fourSteps("error", "done"));
    const failed = row("Tests ausführen");
    expect(failed.dataset.status).toBe("error");
    const cross = failed.querySelector("svg.lucide-x");
    expect(cross).not.toBeNull();
    expect(cross).toHaveClass("text-destructive");
    expect(failed).toHaveClass("text-destructive");

    const next = row("PR öffnen");
    expect(next.dataset.status).toBe("pending");
    expect(next.querySelector("svg.lucide-check")).toBeNull();
    expect(within(next).queryByText(/^\d+(\.\d)?(ms|s)$/)).toBeNull();
    expect(next.querySelector(".font-mono")).toBeNull();

    const rows = screen.getAllByTestId(STEP_TEST_IDS.row);
    const failedIndex = rows.indexOf(failed);
    expect(rows.slice(failedIndex + 1).every((element) => element.dataset.status !== "done")).toBe(
      true,
    );
  });
});

describe("TaskSteps reduced motion", () => {
  it("AC-090b under reduced motion the active row has no spinning arc and no shimmer", () => {
    reducedMotion.value = true;
    renderSteps(fourSteps("active"));
    const active = row("Tests ausführen");
    const arc = active.querySelector("svg.lucide-loader-circle") as SVGElement | null;
    expect(arc).not.toBeNull();
    expect(arc).not.toHaveAttribute("data-spinning");
    expect(arc).not.toHaveClass("animate-spin");
    expect(arc?.style.animationDuration).toBe("");
    expect(active.querySelector(".text-shimmer")).toBeNull();
    expect(active).toHaveAttribute("aria-current", "step");
  });

  it("AC-090b under reduced motion active to done fades in at most 120 ms with no scale", () => {
    reducedMotion.value = true;
    const view = renderSteps(fourSteps("active"));
    view.rerenderSteps(fourSteps("done"));
    const icon = iconMotion("Tests ausführen");
    expect(row("Tests ausführen").querySelector("svg.lucide-check")).not.toBeNull();
    expect(icon.dataset.motion).toBe("step-icon-reduced");
    expect(Number(icon.dataset.motionDuration)).toBeLessThanOrEqual(120);
    expect(Number(icon.dataset.motionDuration)).toBe(REDUCED_FADE_MS);
    expect(icon.style.transform).toBe("");
    expect(icon.style.rotate).toBe("");
    expect(icon.style.scale).toBe("");
  });

  it("AC-090b without reduced motion the same change pops the check from scale 0.4", () => {
    const view = renderSteps(fourSteps("active"));
    view.rerenderSteps(fourSteps("done"));
    const icon = iconMotion("Tests ausführen");
    expect(icon.dataset.motion).toBe("step-icon-pop");
    expect(icon.style.transform).toContain("scale(0.4)");
  });

  it("AC-090b a row's first status mounts static, with no motion preset", () => {
    renderSteps(fourSteps("active"));
    for (const label of TODO_LABELS) {
      expect(iconMotion(label).dataset.motion).toBeUndefined();
    }
  });

  it("AC-090b under reduced motion the working status chip's arc does not turn", () => {
    reducedMotion.value = true;
    render(<AgentStatusChip state={{ kind: "running", source: "session", pending: 0 }} />);
    const arc = screen
      .getByTestId(CHAT_TEST_IDS.agentStatusChip)
      .querySelector("svg.lucide-loader-circle");
    expect(arc).not.toBeNull();
    expect(arc).not.toHaveAttribute("data-spinning");
    expect(arc).not.toHaveClass("animate-spin");
  });

  it("AC-090b without reduced motion the working status chip's arc turns at the step speed", () => {
    render(<AgentStatusChip state={{ kind: "running", source: "session", pending: 0 }} />);
    const arc = screen
      .getByTestId(CHAT_TEST_IDS.agentStatusChip)
      .querySelector("svg.lucide-loader-circle") as SVGElement | null;
    expect(arc).toHaveAttribute("data-spinning", "true");
    expect(arc?.style.animationDuration).toBe(`${spinSeconds}s`);
  });
});
