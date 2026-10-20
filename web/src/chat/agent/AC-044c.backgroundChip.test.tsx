// AC-044c: a session that waits with no open approval works in the
// background. It shows "arbeitet im Hintergrund" with the icon and border of
// "arbeitet", never the "braucht Antwort" accent.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { CHAT_TEST_IDS } from "../testids";
import { AgentStatusChip } from "./AgentStatusChip";
import { AGENT_CHIP_LABELS, agentChipState } from "./agentStatus";

afterEach(cleanup);

describe("AC-044c waiting without an open approval", () => {
  it("AC-044c waiting and pending 0 shows arbeitet im Hintergrund", () => {
    const state = agentChipState({
      agentState: "active",
      sessionStatus: "waiting",
      pendingElicitations: 0,
    });
    expect(state).toEqual({ kind: "background", source: "session", pending: 0 });
    render(<AgentStatusChip state={state!} />);
    const chip = screen.getByTestId(CHAT_TEST_IDS.agentStatusChip);
    expect(chip.textContent).toBe("arbeitet im Hintergrund");
    expect(chip.textContent).not.toBe(AGENT_CHIP_LABELS.waiting);
    expect(chip.className).not.toContain("warning");
  });

  it("AC-044c the same session with an open approval shows braucht Antwort", () => {
    const state = agentChipState({
      agentState: "active",
      sessionStatus: "waiting",
      pendingElicitations: 1,
    });
    render(<AgentStatusChip state={state!} />);
    expect(screen.getByTestId(CHAT_TEST_IDS.agentStatusChip).textContent).toBe("braucht Antwort");
  });
});
