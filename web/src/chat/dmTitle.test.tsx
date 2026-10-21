import { describe, expect, it } from "vitest";

import type { DirectoryEntry } from "./chatApi";
import { dmHeading, dmParticipantLine } from "./dmTitle";

const directory = new Map<string, DirectoryEntry>([
  ["anna", { id: "anna", kind: "user", display_name: "Anna" }],
  ["bob", { id: "bob", kind: "user", handle: "bobby" }],
  ["jens", { id: "jens", kind: "user", display_name: "Jens" }],
]);

function members(...ids: string[]) {
  return ids.map((principal_id) => ({ principal_id }));
}

describe("dmHeading", () => {
  it("is Direktnachricht when the caller is alone", () => {
    expect(dmHeading("jens", members("jens"), directory)).toBe("Direktnachricht");
  });

  it("joins the other members by directory label and leaves out the caller", () => {
    expect(dmHeading("jens", members("jens", "anna", "bob", "carla"), directory)).toBe(
      "Anna, bobby, carla",
    );
  });

  it("skips an empty principal id", () => {
    expect(dmHeading("jens", members("", "jens", "anna"), directory)).toBe("Anna");
  });
});

describe("dmParticipantLine", () => {
  it("lists every member and labels the caller (du)", () => {
    expect(dmParticipantLine("jens", members("jens", "anna"), directory)).toBe(
      "Teilnehmer: Jens (du), Anna",
    );
  });

  it("skips an empty principal id", () => {
    expect(dmParticipantLine("jens", members("anna", ""), directory)).toBe("Teilnehmer: Anna");
  });
});
