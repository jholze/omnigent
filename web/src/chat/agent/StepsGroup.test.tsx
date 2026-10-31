// Collapsed agent steps in an open Agent-Thread (FR-048, FR-089, FR-090).
// Bubbles and todos are written as the thread's session would hold them;
// todo durations and the 500 ms announcement run on fake timers.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Bubble, RenderItem } from "@/lib/renderItems";
import type * as MotionRootModule from "../MotionRoot";
import {
  ANNOUNCE_DEBOUNCE_MS,
  StepsGroup,
  currentTurnToolSteps,
  stepsSummaryText,
  todoSteps,
  type SessionTodo,
} from "./StepsGroup";
import { STEP_TEST_IDS } from "./TaskSteps";

const reducedMotion = vi.hoisted(() => ({ value: false }));
vi.mock("../MotionRoot", async (importOriginal) => ({
  ...(await importOriginal<typeof MotionRootModule>()),
  useChatReducedMotion: () => reducedMotion.value,
}));

type ToolItem = Extract<RenderItem, { kind: "tool" }>;

function tool(callId: string, name: string, state: ToolItem["state"], duration?: number): ToolItem {
  return {
    kind: "tool",
    itemId: `item_${callId}`,
    execution: {
      name,
      arguments: {},
      argsSummary: "",
      callId,
      agentName: "coder",
      executedBy: "server",
      output: state === "input-available" ? null : "ok",
    },
    output: state === "input-available" ? null : "ok",
    state,
    startedAt: null,
    duration,
  };
}

function userBubble(itemId: string): Bubble {
  return { kind: "user", itemId, content: [{ type: "input_text", text: "Bitte fixen" }] };
}

function assistantBubble(responseId: string, items: RenderItem[]): Bubble {
  return {
    kind: "assistant",
    responseId,
    stableId: responseId,
    lifecycle: "streaming",
    error: null,
    items,
  };
}

/** An earlier turn with one tool, then the current turn with three. */
const threeToolTurn: Bubble[] = [
  userBubble("u_old"),
  assistantBubble("resp_old", [tool("call_old", "lookup_old_turn", "output-available", 0.5)]),
  userBubble("u_now"),
  assistantBubble("resp_now", [
    tool("call_1", "grep_repo", "output-available", 0.4),
    tool("call_2", "apply_fix", "output-available", 1.1),
    tool("call_3", "run_pytest", "input-available"),
  ]),
];

function todo(content: string, status: SessionTodo["status"]): SessionTodo {
  return { content, status, activeForm: content };
}

const LABELS = ["Tests lesen", "Fix schreiben", "Tests ausführen", "PR öffnen"] as const;

/** The four todos with the first *done* completed and the next one in progress. */
function plan(done: number): SessionTodo[] {
  return LABELS.map((label, index) =>
    todo(label, index < done ? "completed" : index === done ? "in_progress" : "pending"),
  );
}

function renderGroup(props: { bubbles?: Bubble[]; todos?: SessionTodo[]; working?: boolean }) {
  const current = { bubbles: [] as Bubble[], todos: [] as SessionTodo[], working: true, ...props };
  const view = render(<StepsGroup {...current} />);
  return {
    ...view,
    update: (next: Partial<typeof current>) => {
      Object.assign(current, next);
      view.rerender(<StepsGroup {...current} />);
    },
  };
}

function statusRegion(): HTMLElement {
  return within(screen.getByTestId("steps-group")).getByRole("status");
}

function row(label: string): HTMLElement {
  const match = screen
    .getAllByTestId(STEP_TEST_IDS.row)
    .find((element) => element.textContent?.includes(label));
  if (match === undefined) throw new Error(`no step row for ${label}`);
  return match;
}

function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

beforeEach(() => {
  reducedMotion.value = false;
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("StepsGroup from the current turn's tool calls", () => {
  it("AC-048a three tool calls in the turn show 3 Schritte and Enter expands the ordered list", async () => {
    const user = userEvent.setup();
    renderGroup({ bubbles: threeToolTurn, working: true });
    const summary = screen.getByTestId(STEP_TEST_IDS.summary);
    expect(summary).toHaveTextContent("3 Schritte");
    expect(summary).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId(STEP_TEST_IDS.list)).toBeNull();

    summary.focus();
    await user.keyboard("{Enter}");

    expect(summary).toHaveAttribute("aria-expanded", "true");
    const list = screen.getByTestId(STEP_TEST_IDS.list);
    expect(list.tagName).toBe("OL");
    expect(summary).toHaveAttribute("aria-controls", list.id);
    const rows = within(list).getAllByTestId(STEP_TEST_IDS.row);
    expect(rows.map((element) => element.textContent?.replace(/\d+(\.\d)?(ms|s)$/, ""))).toEqual([
      "grep_repo",
      "apply_fix",
      "run_pytest",
    ]);
    expect(rows.map((element) => element.dataset.status)).toEqual(["done", "done", "active"]);

    await user.keyboard("{Enter}");
    expect(summary).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId(STEP_TEST_IDS.list)).toBeNull();
  });

  it("AC-048a only the current turn counts: tools before the last user input are left out", () => {
    const steps = currentTurnToolSteps(threeToolTurn, true);
    expect(steps.map((step) => step.label)).toEqual(["grep_repo", "apply_fix", "run_pytest"]);
    expect(stepsSummaryText(steps.length)).toBe("3 Schritte");
    expect(stepsSummaryText(1)).toBe("1 Schritt");
  });

  it("AC-048a a turn without tool calls or todos renders no steps row", () => {
    renderGroup({ bubbles: [userBubble("u_now"), assistantBubble("resp_now", [])] });
    expect(screen.queryByTestId("steps-group")).toBeNull();
  });
});

describe("StepsGroup from the session's todos", () => {
  it("AC-048b expanded todos: checks with mono durations, spinning shimmering current step, dot for PR öffnen", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const view = renderGroup({ todos: plan(0) });
    advance(3400);
    view.update({ todos: plan(1) });
    advance(1200);
    view.update({ todos: plan(2) });

    const summary = screen.getByTestId(STEP_TEST_IDS.summary);
    expect(summary).toHaveTextContent("4 Schritte");
    fireEvent.click(summary);

    for (const [label, duration] of [
      ["Tests lesen", "3.4s"],
      ["Fix schreiben", "1.2s"],
    ] as const) {
      const done = row(label);
      expect(done.dataset.status).toBe("done");
      expect(done.querySelector("svg.lucide-check")).not.toBeNull();
      expect(within(done).getByText(duration)).toHaveClass("font-mono");
    }
    const active = row("Tests ausführen");
    expect(active.dataset.status).toBe("active");
    expect(active.querySelector("svg.lucide-loader-circle")).toHaveAttribute(
      "data-spinning",
      "true",
    );
    expect(active.querySelector(".text-shimmer")).toHaveTextContent("Tests ausführen");
    const pending = row("PR öffnen");
    expect(pending.dataset.status).toBe("pending");
    expect(pending.querySelector("svg")).toBeNull();
    expect(pending.querySelector(".rounded-full")).not.toBeNull();
  });

  it("AC-048b todos win over tool calls of the same turn", () => {
    renderGroup({ bubbles: threeToolTurn, todos: plan(2) });
    expect(screen.getByTestId(STEP_TEST_IDS.summary)).toHaveTextContent("4 Schritte");
  });

  it("AC-048b a todo already done when the thread opened shows no duration", () => {
    const steps = todoSteps(plan(2), {});
    expect(steps.map((step) => [step.label, step.status, step.durationS])).toEqual([
      ["Tests lesen", "done", null],
      ["Fix schreiben", "done", null],
      ["Tests ausführen", "active", null],
      ["PR öffnen", "pending", null],
    ]);
  });
});

describe("StepsGroup announcements", () => {
  it("AC-090a moving to step 3 announces Tests ausführen, Schritt 3 von 4 after the 500 ms debounce", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const view = renderGroup({ todos: plan(1) });
    advance(ANNOUNCE_DEBOUNCE_MS);
    fireEvent.click(screen.getByTestId(STEP_TEST_IDS.summary));
    expect(statusRegion()).toHaveTextContent("Fix schreiben, Schritt 2 von 4");
    expect(statusRegion()).toHaveAttribute("aria-live", "polite");
    expect(statusRegion()).toHaveClass("sr-only");

    view.update({ todos: plan(2) });
    expect(row("Tests ausführen")).toHaveAttribute("aria-current", "step");
    expect(row("Fix schreiben")).not.toHaveAttribute("aria-current");
    advance(ANNOUNCE_DEBOUNCE_MS - 1);
    expect(statusRegion()).toHaveTextContent("Fix schreiben, Schritt 2 von 4");
    advance(1);
    expect(statusRegion().textContent).toBe("Tests ausführen, Schritt 3 von 4");
  });

  it("AC-090a a step skipped within 500 ms is never announced", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const view = renderGroup({ todos: plan(1) });
    advance(ANNOUNCE_DEBOUNCE_MS);
    view.update({ todos: plan(2) });
    advance(200);
    view.update({ todos: plan(3) });
    advance(ANNOUNCE_DEBOUNCE_MS);
    expect(statusRegion().textContent).toBe("PR öffnen, Schritt 4 von 4");
  });

  it("AC-090a when every todo is completed the polite region says Lauf abgeschlossen", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const view = renderGroup({ todos: plan(3) });
    advance(ANNOUNCE_DEBOUNCE_MS);
    view.update({ todos: plan(4) });
    advance(ANNOUNCE_DEBOUNCE_MS);
    expect(statusRegion().textContent).toBe("Lauf abgeschlossen");
    expect(statusRegion()).toHaveAttribute("aria-live", "polite");
  });

  it("AC-090a the status region is there while the list is collapsed", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    renderGroup({ todos: plan(2) });
    advance(ANNOUNCE_DEBOUNCE_MS);
    expect(screen.queryByTestId(STEP_TEST_IDS.list)).toBeNull();
    expect(statusRegion().textContent).toBe("Tests ausführen, Schritt 3 von 4");
  });
});
