import { describe, expect, it } from "vitest";

import type { ChannelMember, DirectoryEntry } from "./chatApi";
import { filterMentionCandidates, mentionCandidates, mentionToken } from "./mentions";

const ANNA = "anna@team-chat.test";
const JENS = "jens@team-chat.test";
const BOB = "bob@team-chat.test";

const members: ChannelMember[] = [
  { principal_id: ANNA, kind: "human", display: "Anna" },
  { principal_id: JENS, kind: "human", display: "Jens" },
];

const directory = new Map<string, DirectoryEntry>([
  [ANNA, { id: ANNA, kind: "user", display_name: "Anna" }],
  [JENS, { id: JENS, kind: "user", display_name: "Jens" }],
  [BOB, { id: BOB, kind: "user", display_name: "Bob" }],
]);

describe("mentionCandidates", () => {
  it("AC-026c puts the Omnigent agent row first", () => {
    const [first] = mentionCandidates(members, directory, ANNA);
    expect(first).toMatchObject({ kind: "omnigent_agent", id: "agent", label: "agent" });
  });

  it("AC-026c excludes the caller", () => {
    const ids = mentionCandidates(members, directory, ANNA).map((candidate) => candidate.id);
    expect(ids).not.toContain(ANNA);
    expect(ids).toEqual(["agent", BOB, JENS]);
  });
});

describe("filterMentionCandidates", () => {
  const candidates = mentionCandidates(members, directory, ANNA);

  it("AC-026c prefix je matches Jens, case-insensitive", () => {
    expect(filterMentionCandidates(candidates, "je").map((candidate) => candidate.id)).toEqual([
      JENS,
    ]);
    expect(filterMentionCandidates(candidates, "JE").map((candidate) => candidate.id)).toEqual([
      JENS,
    ]);
  });

  it("AC-026e prefix zz matches nobody", () => {
    expect(filterMentionCandidates(candidates, "zz")).toEqual([]);
  });

  it("AC-026c prefix ag matches the agent row", () => {
    expect(filterMentionCandidates(candidates, "ag").map((candidate) => candidate.kind)).toEqual([
      "omnigent_agent",
    ]);
  });
});

describe("mentionToken", () => {
  it("AC-026g serializes a user and the Omnigent agent", () => {
    expect(mentionToken({ kind: "user", id: JENS })).toBe(`<@user:${JENS}>`);
    expect(mentionToken({ kind: "omnigent_agent", id: "agent" })).toBe("<@omnigent_agent:agent>");
  });
});
