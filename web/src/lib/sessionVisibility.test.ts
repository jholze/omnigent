import { describe, expect, it } from "vitest";
import type { Conversation } from "@/hooks/useConversations";
import {
  filterSessionScope,
  isAgentThreadSession,
  sessionVisibility,
  showsInMainSidebar,
} from "./sessionVisibility";

const THREAD_LABELS = { "omnigent.chat.v1": "c-general:m-root" };

describe("frontend session scope", () => {
  it.each([
    [{ owner: "alice", permission_level: null }, "mine"],
    [{ owner: "bob", permission_level: null }, "shared"],
    [{ owner: "alice", permission_level: 1 }, "mine"],
    [{ owner: "bob", permission_level: 4 }, "shared"],
    [{ permission_level: 4 }, "mine"],
    [{ permission_level: 1 }, "shared"],
    [{ permission_level: 2 }, "shared"],
    [{ permission_level: 3 }, "shared"],
    [{ permission_level: null }, "mine"],
  ] as const)("classifies %j as %s", (row, expected) => {
    expect(sessionVisibility(row, "alice")).toBe(expected);
  });
  it("does not invent shared ownership without viewer identity or a grant", () => {
    expect(sessionVisibility({ owner: "alice", permission_level: null }, null)).toBe("mine");
    expect(sessionVisibility({ permission_level: 1 }, null)).toBe("shared");
    expect(sessionVisibility({ owner: "bob", permission_level: 4 }, null)).toBe("mine");
  });
  it("separates a mixed response and excludes archived rows", () => {
    const rows = [
      { id: "mine", owner: "alice", permission_level: null },
      { id: "shared", owner: "bob", permission_level: 4 },
      { id: "archived", owner: "alice", permission_level: 4, archived: true },
    ] as Conversation[];
    expect(filterSessionScope(rows, "mine", "alice").map((r) => r.id)).toEqual(["mine"]);
    expect(filterSessionScope(rows, "shared", "alice").map((r) => r.id)).toEqual(["shared"]);
  });
});

describe("Agent-Thread sessions in the main sidebar", () => {
  it("AC-029c isAgentThreadSession is true only with the omnigent.chat.v1 label", () => {
    expect(isAgentThreadSession({ labels: THREAD_LABELS })).toBe(true);
    expect(isAgentThreadSession({ labels: { "omnigent.wrapper": "claude" } })).toBe(false);
    expect(isAgentThreadSession({ labels: {} })).toBe(false);
    expect(isAgentThreadSession({ labels: null } as unknown as Conversation)).toBe(false);
  });

  it("AC-085b the owner still sees a labelled Agent-Thread session", () => {
    const row = { labels: THREAD_LABELS, owner: "alice", permission_level: 4 };
    expect(showsInMainSidebar(row, "alice")).toBe(true);
  });

  it("AC-085a a non-owner with a grant does not see a labelled Agent-Thread session", () => {
    for (const level of [1, 2, 3]) {
      const row = { labels: THREAD_LABELS, owner: "alice", permission_level: level };
      expect(showsInMainSidebar(row, "bob")).toBe(false);
    }
  });

  it("AC-085a an unlabelled session is shown to owner and non-owner alike", () => {
    const row = { labels: {}, owner: "alice", permission_level: 1 };
    expect(showsInMainSidebar(row, "alice")).toBe(true);
    expect(showsInMainSidebar(row, "bob")).toBe(true);
  });
});
