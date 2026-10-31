/**
 * T-117 wiring: which chat elements animate, with which preset, and that
 * reduced motion swaps every transform for a ≤ 120 ms fade (FR-078, FR-079).
 */
import { cleanup, render, renderHook, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentStatusChip } from "./agent/AgentStatusChip";
import type { AgentChipState } from "./agent/agentStatus";
import { CardMount } from "./agent/CardMount";
import type { DirectoryEntry } from "./chatApi";
import { MessageItem, type MessageView } from "./MessageItem";
import { LIVE_BATCH_MAX, useEnterKeys } from "./MessageList";
import { MotionRoot } from "./MotionRoot";
import type * as MotionRootModule from "./MotionRoot";
import { usePaneOpenMotion } from "./ThreadPane";
import { CHAT_TEST_IDS } from "./testids";

const reducedMotion = vi.hoisted(() => ({ value: false }));
vi.mock("./MotionRoot", async (importOriginal) => ({
  ...(await importOriginal<typeof MotionRootModule>()),
  useChatReducedMotion: () => reducedMotion.value,
}));

const directory = new Map<string, DirectoryEntry>([
  ["anna", { id: "anna", kind: "user", display_name: "Anna" }],
]);

let clock = 1_000_000;
function view(id: string, overrides: Partial<MessageView> = {}): MessageView {
  clock += 1000;
  return {
    key: id,
    id,
    clientId: null,
    channelId: "c1",
    threadRootId: null,
    authorId: "anna",
    authorKind: "user",
    createdAtMs: clock,
    body: "Hallo",
    status: "sent",
    ...overrides,
  };
}

function renderMessage(enter: boolean) {
  return render(
    <MotionRoot>
      <MemoryRouter>
        <ol>
          <MessageItem message={view("m1")} directory={directory} enter={enter} />
        </ol>
      </MemoryRouter>
    </MotionRoot>,
  );
}

beforeEach(() => {
  reducedMotion.value = false;
});
afterEach(() => cleanup());

describe("MessageItem enter motion", () => {
  it("AC-078a a live message mounts with the 180 ms transform preset", () => {
    renderMessage(true);
    const row = screen.getByTestId(CHAT_TEST_IDS.message);
    expect(row.dataset.motion).toBe("message-enter");
    expect(Math.abs(Number(row.dataset.motionDuration) - 180)).toBeLessThanOrEqual(20);
  });

  it("AC-079a reduced motion: the live message only fades, ≤ 120 ms", () => {
    reducedMotion.value = true;
    renderMessage(true);
    const row = screen.getByTestId(CHAT_TEST_IDS.message);
    expect(row.dataset.motion).toBe("message-enter-reduced");
    expect(Number(row.dataset.motionDuration)).toBeLessThanOrEqual(120);
    expect(row.style.transform).toBe("");
  });

  it("NFR-006 history rows mount static", () => {
    renderMessage(false);
    const row = screen.getByTestId(CHAT_TEST_IDS.message);
    expect(row.dataset.motion).toBeUndefined();
    expect(row.style.opacity).toBe("");
  });
});

describe("useEnterKeys", () => {
  it("FR-078 the first page is history; a later message from someone else enters", () => {
    const first = [view("a"), view("b")];
    const { result, rerender } = renderHook(({ messages }) => useEnterKeys(messages, "me"), {
      initialProps: { messages: first },
    });
    expect(result.current.size).toBe(0);
    const next = [...first, view("c")];
    rerender({ messages: next });
    expect([...result.current]).toEqual(["c"]);
    // The decision sticks across re-renders.
    rerender({ messages: [...next] });
    expect([...result.current]).toEqual(["c"]);
  });

  it("NFR-006 own sends (optimistic and confirmed) never enter", () => {
    const { result, rerender } = renderHook(({ messages }) => useEnterKeys(messages, "me"), {
      initialProps: { messages: [view("a")] },
    });
    const pending = view("client:x", {
      id: null,
      clientId: "x",
      authorId: "me",
      status: "sending",
    });
    rerender({ messages: [view("a"), pending] });
    expect(result.current.size).toBe(0);
    rerender({ messages: [view("a"), view("x", { authorId: "me" })] });
    expect(result.current.size).toBe(0);
  });

  it("FR-078 backfill bursts and scroll-back pages stay static", () => {
    const base = [view("a"), view("b")];
    const { result, rerender } = renderHook(({ messages }) => useEnterKeys(messages, "me"), {
      initialProps: { messages: base },
    });
    const burst = Array.from({ length: LIVE_BATCH_MAX + 1 }, (_, i) => view(`burst-${i}`));
    rerender({ messages: [...base, ...burst] });
    expect(result.current.size).toBe(0);
    const older = view("old", { createdAtMs: 1 });
    rerender({ messages: [older, ...base, ...burst] });
    expect(result.current.size).toBe(0);
  });
});

function chip(kind: AgentChipState["kind"]): AgentChipState {
  return { kind, source: "session", pending: 0 };
}

describe("AgentStatusChip crossfade", () => {
  it("FR-078 the first label is static; a status change crossfades 200 ms", () => {
    const { rerender } = render(
      <MotionRoot>
        <AgentStatusChip state={chip("running")} />
      </MotionRoot>,
    );
    const root = screen.getByTestId(CHAT_TEST_IDS.agentStatusChip);
    expect(root.querySelector("[data-motion]")).toBeNull();
    expect(root.style.transitionProperty).toBe("color, background-color, border-color");

    rerender(
      <MotionRoot>
        <AgentStatusChip state={chip("waiting")} />
      </MotionRoot>,
    );
    const label = root.querySelector<HTMLElement>("[data-motion]");
    expect(label?.dataset.motion).toBe("chip-crossfade");
    expect(label?.dataset.motionDuration).toBe("200");
    expect(root.textContent).toBe("braucht Antwort");
  });

  it("AC-079a reduced motion: the status change only fades ≤ 120 ms", () => {
    reducedMotion.value = true;
    const { rerender } = render(
      <MotionRoot>
        <AgentStatusChip state={chip("running")} />
      </MotionRoot>,
    );
    rerender(
      <MotionRoot>
        <AgentStatusChip state={chip("idle")} />
      </MotionRoot>,
    );
    const root = screen.getByTestId(CHAT_TEST_IDS.agentStatusChip);
    const label = root.querySelector<HTMLElement>("[data-motion]");
    expect(label?.dataset.motion).toBe("chip-crossfade-reduced");
    expect(Number(label?.dataset.motionDuration)).toBeLessThanOrEqual(120);
    expect(root.style.transitionDuration).toBe("120ms");
  });
});

describe("usePaneOpenMotion", () => {
  const original = window.matchMedia;
  function desktop(matches: boolean) {
    window.matchMedia = ((query: string) => ({
      ...original(query),
      matches: query.includes("min-width") ? matches : false,
    })) as typeof window.matchMedia;
  }
  afterEach(() => {
    window.matchMedia = original;
  });

  it("FR-078 the desktop column slides in over 220 ms", () => {
    desktop(true);
    const { result } = renderHook(() => usePaneOpenMotion());
    expect(result.current).toMatchObject({
      "data-motion": "pane-open",
      "data-motion-duration": 220,
      initial: { transform: "translateX(16px)" },
    });
  });

  it("AC-079a reduced motion: the pane fades ≤ 120 ms without transform", () => {
    desktop(true);
    reducedMotion.value = true;
    const { result } = renderHook(() => usePaneOpenMotion());
    expect(result.current).toMatchObject({ "data-motion": "pane-open-reduced" });
    expect(JSON.stringify(result.current)).not.toContain("transform");
  });

  it("phone layout mounts the pane static", () => {
    desktop(false);
    const { result } = renderHook(() => usePaneOpenMotion());
    expect(result.current).toEqual({ initial: false });
  });
});

describe("CardMount", () => {
  it("FR-078 a live approval card springs in, settling ≤ 300 ms", () => {
    render(
      <MotionRoot>
        <CardMount animate>card</CardMount>
      </MotionRoot>,
    );
    const el = screen.getByText("card");
    expect(el.dataset.motion).toBe("card-mount");
    expect(Number(el.dataset.motionDuration)).toBeLessThanOrEqual(300);
  });

  it("AC-079a reduced motion: the card fades ≤ 120 ms", () => {
    reducedMotion.value = true;
    render(
      <MotionRoot>
        <CardMount animate>card</CardMount>
      </MotionRoot>,
    );
    const el = screen.getByText("card");
    expect(el.dataset.motion).toBe("card-mount-reduced");
    expect(Number(el.dataset.motionDuration)).toBeLessThanOrEqual(120);
  });

  it("a card that was already there mounts static", () => {
    render(<CardMount animate={false}>card</CardMount>);
    expect(screen.getByText("card").dataset.motion).toBeUndefined();
  });
});
