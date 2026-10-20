import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { parseChatFrame } from "../useChatFrames";
import { CHAT_TEST_IDS } from "../testids";
import { AgentStatusChip } from "./AgentStatusChip";
import { AGENT_CHIP_LABELS, AGENT_CHIP_WIDEST_LABEL, agentChipState } from "./agentStatus";
import { useAgentThreadStore } from "./agentThreadStore";
import type { ChatMessage } from "../chatApi";
import { parseEvent } from "@/lib/sse";

afterEach(cleanup);

describe("agentChipState (status mapping, 03-domain)", () => {
  it.each([
    ["launching", "startet…"],
    ["running", "arbeitet"],
    ["waiting", "arbeitet im Hintergrund"],
    ["idle", "fertig"],
    ["failed", "Fehler"],
  ])("FR-044 session status %s with no open approval shows %s", (status, label) => {
    const state = agentChipState({ agentState: "active", sessionStatus: status });
    expect(state?.source).toBe("session");
    expect(state && AGENT_CHIP_LABELS[state.kind]).toBe(label);
  });

  it("AC-044a waiting with an open approval shows braucht Antwort", () => {
    const state = agentChipState({
      agentState: "active",
      sessionStatus: "waiting",
      pendingElicitations: 1,
    });
    expect(state).toEqual({ kind: "waiting", source: "session", pending: 1 });
    expect(AGENT_CHIP_LABELS.waiting).toBe("braucht Antwort");
  });

  // AC-044c (waiting, pending 0) lives in AC-044c.backgroundChip.test.tsx.

  it("FR-047 a running row with no open approval stays arbeitet", () => {
    expect(
      agentChipState({ agentState: "active", sessionStatus: "running", pendingElicitations: 0 })
        ?.kind,
    ).toBe("running");
  });

  it("FR-044 agent_state dismissed shows entlassen, whatever the session does", () => {
    const state = agentChipState({ agentState: "dismissed", sessionStatus: "running" });
    expect(state?.kind).toBe("dismissed");
    expect(AGENT_CHIP_LABELS.dismissed).toBe("entlassen");
  });

  it("FR-047 an open approval on a running row shows braucht Antwort", () => {
    const state = agentChipState({
      agentState: "active",
      sessionStatus: "running",
      pendingElicitations: 2,
    });
    expect(state).toEqual({ kind: "waiting", source: "session", pending: 2 });
  });

  it("a failed session stays Fehler even with an open approval", () => {
    expect(agentChipState({ sessionStatus: "failed", pendingElicitations: 1 })?.kind).toBe(
      "failed",
    );
  });

  it.each([
    ["starting", "launching"],
    ["active", "active"],
    ["start_failed", "startFailed"],
    ["dismissed", "dismissed"],
    ["closed", "closed"],
  ])("AC-047b without a session status, agent_state %s maps to %s", (agentState, kind) => {
    const state = agentChipState({ agentState });
    expect(state).toEqual({ kind, source: "thread", pending: 0 });
  });

  it("AC-047b thread labels: start_failed is not the live Fehler", () => {
    expect(AGENT_CHIP_LABELS.launching).toBe("startet…");
    expect(AGENT_CHIP_LABELS.active).toBe("aktiv");
    expect(AGENT_CHIP_LABELS.dismissed).toBe("entlassen");
    expect(AGENT_CHIP_LABELS.startFailed).toBe("Start fehlgeschlagen");
    expect(AGENT_CHIP_LABELS.failed).toBe("Fehler");
  });

  it("no chip for a plain thread or when nothing is known", () => {
    expect(agentChipState({ agentState: "none" })).toBeNull();
    expect(agentChipState({})).toBeNull();
    expect(agentChipState({ sessionStatus: "unknown" })).toBeNull();
  });
});

describe("AgentStatusChip", () => {
  it("arbeitet im Hintergrund shares the arbeitet icon and border, not the warning accent", () => {
    const { rerender } = render(
      <AgentStatusChip state={{ kind: "running", source: "session", pending: 0 }} />,
    );
    const running = screen.getByTestId(CHAT_TEST_IDS.agentStatusChip);
    const runningClass = running.className;
    const runningIcon = running.querySelector("svg")?.getAttribute("class");
    rerender(<AgentStatusChip state={{ kind: "background", source: "session", pending: 0 }} />);
    const chip = screen.getByTestId(CHAT_TEST_IDS.agentStatusChip);
    expect(chip.textContent).toBe("arbeitet im Hintergrund");
    expect(chip.className).toBe(runningClass);
    expect(chip.className).not.toContain("warning");
    expect(chip.querySelector("svg")?.getAttribute("class")).toBe(runningIcon);
  });

  it("the width sizer reserves the widest label and lets a narrow row truncate", () => {
    render(<AgentStatusChip state={{ kind: "idle", source: "session", pending: 0 }} />);
    const chip = screen.getByTestId(CHAT_TEST_IDS.agentStatusChip);
    const sizer = chip.querySelector('[aria-hidden="true"].invisible');
    expect(sizer?.className).toContain(
      `before:content-['${AGENT_CHIP_WIDEST_LABEL.replaceAll(" ", "_")}']`,
    );
    expect(chip.className).toContain("max-w-full");
    expect(chip.textContent).toBe("fertig");
    for (const label of Object.values(AGENT_CHIP_LABELS)) {
      expect(label.length).toBeLessThanOrEqual(AGENT_CHIP_WIDEST_LABEL.length);
    }
  });

  it("text content is exactly the verbatim label", () => {
    render(<AgentStatusChip state={{ kind: "waiting", source: "session", pending: 1 }} />);
    const chip = screen.getByTestId(CHAT_TEST_IDS.agentStatusChip);
    expect(chip.textContent).toBe("braucht Antwort");
    expect(chip).toHaveAttribute("data-state", "waiting");
    expect(chip.getAttribute("title")).toContain("1 Freigabe offen");
    expect(chip.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("a thread-state chip says it is not the live status", () => {
    render(<AgentStatusChip state={{ kind: "active", source: "thread", pending: 0 }} />);
    const chip = screen.getByTestId(CHAT_TEST_IDS.agentStatusChip);
    expect(chip.textContent).toBe("aktiv");
    expect(chip.getAttribute("title")).toContain("Letzter bekannter Thread-Zustand");
  });
});

describe("AC-047b cold load seeds the thread store from agent_state", () => {
  function root(overrides: Partial<ChatMessage>): ChatMessage {
    return {
      id: "m-root",
      channel_id: "c1",
      thread_root_id: null,
      author_id: "anna",
      author_kind: "user",
      created_at: 1,
      body: "hi",
      client_id: null,
      ...overrides,
    };
  }

  it("a thread root with agent_state seeds the last known state, without a session id", () => {
    useAgentThreadStore.setState({ threads: {}, sessions: {}, watchers: {} });
    useAgentThreadStore
      .getState()
      .seedFromMessages([
        root({ agent_state: "start_failed" }),
        root({ id: "m-plain", agent_state: "none" }),
        root({ id: "m-reply", thread_root_id: "m-root", agent_state: "active" }),
        root({ id: "m-old" }),
      ]);
    expect(useAgentThreadStore.getState().threads).toEqual({
      "m-root": { channelId: "c1", rootId: "m-root", agentState: "start_failed", sessionId: null },
    });
  });

  it("a live thread frame wins; a label-known session id is kept", () => {
    useAgentThreadStore.setState({
      threads: {
        "m-root": { channelId: "c1", rootId: "m-root", agentState: "active", sessionId: "s1" },
        "m-two": { channelId: "c1", rootId: "m-two", agentState: null, sessionId: "s2" },
      },
      sessions: {},
      watchers: {},
    });
    useAgentThreadStore
      .getState()
      .seedFromMessages([
        root({ agent_state: "starting" }),
        root({ id: "m-two", agent_state: "dismissed" }),
      ]);
    const threads = useAgentThreadStore.getState().threads;
    expect(threads["m-root"].agentState).toBe("active");
    expect(threads["m-two"]).toEqual({
      channelId: "c1",
      rootId: "m-two",
      agentState: "dismissed",
      sessionId: "s2",
    });
  });
});

describe("response.elicitation_resolved decided_by", () => {
  it("parses decided_by together with a verdict", () => {
    expect(
      parseEvent("response.elicitation_resolved", {
        elicitation_id: "e1",
        action: "accept",
        decided_by: "u_bob",
      }),
    ).toEqual({
      type: "elicitation_resolved",
      elicitationId: "e1",
      action: "accept",
      decidedBy: "u_bob",
    });
  });

  it("drops decided_by without a verdict", () => {
    expect(
      parseEvent("response.elicitation_resolved", { elicitation_id: "e1", decided_by: "u_bob" }),
    ).toEqual({ type: "elicitation_resolved", elicitationId: "e1" });
  });
});

describe("parseChatFrame thread.updated", () => {
  it("parses the body-less frame and drops anything else", () => {
    const parsed = parseChatFrame({
      type: "chat",
      kind: "thread.updated",
      channel_id: "c1",
      root_message_id: "m1",
      reply_count: 3,
      agent_state: "active",
      session_id: "conv_1",
      bot_streak: 0,
      body: "secret",
    });
    expect(parsed).toEqual({
      type: "chat",
      kind: "thread.updated",
      channel_id: "c1",
      root_message_id: "m1",
      reply_count: 3,
      agent_state: "active",
      session_id: "conv_1",
      bot_streak: 0,
    });
  });

  it("keeps a starting thread without a session and rejects malformed frames", () => {
    expect(
      parseChatFrame({
        type: "chat",
        kind: "thread.updated",
        channel_id: "c1",
        root_message_id: "m1",
        reply_count: 0,
        agent_state: "starting",
        session_id: null,
        bot_streak: 0,
      }),
    ).toMatchObject({ session_id: null, agent_state: "starting" });
    expect(
      parseChatFrame({
        type: "chat",
        kind: "thread.updated",
        channel_id: "c1",
        reply_count: 0,
        agent_state: "active",
      }),
    ).toBeNull();
    expect(
      parseChatFrame({
        type: "chat",
        kind: "thread.updated",
        channel_id: "c1",
        root_message_id: "m1",
        reply_count: "3",
        agent_state: "active",
      }),
    ).toBeNull();
  });
});
