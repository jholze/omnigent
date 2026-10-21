import { describe, expect, it } from "vitest";

import type { ChatMessage, MessagePageResponse } from "./chatApi";
import { mergeMessagePage } from "./messagePage";

function message(id: string, createdAt: number): ChatMessage {
  return {
    id,
    channel_id: "d-1",
    thread_root_id: null,
    author_id: "anna",
    author_kind: "user",
    created_at: createdAt,
    body: id,
    client_id: null,
  };
}

function page(items: ChatMessage[]): MessagePageResponse {
  return { items, has_older: false, has_more: false, next_after: null, next_before: null };
}

describe("mergeMessagePage", () => {
  it("keeps a sent row when a stale empty page arrives afterwards", () => {
    const sent = message("m1", 20);
    const merged = mergeMessagePage(page([sent]), page([]));
    expect(merged.items.map((item) => item.id)).toEqual(["m1"]);
  });

  it("keeps a row a stale older tail did not include", () => {
    const older = message("m0", 10);
    const sent = message("m1", 20);
    const merged = mergeMessagePage(page([sent]), page([older]));
    expect(merged.items.map((item) => item.id)).toEqual(["m0", "m1"]);
  });

  it("drops a row that is older than the incoming tail", () => {
    const old = message("m0", 10);
    const newer = message("m2", 30);
    const merged = mergeMessagePage(page([old]), page([newer]));
    expect(merged.items.map((item) => item.id)).toEqual(["m2"]);
    expect(merged.has_older).toBe(false);
  });

  it("returns the incoming page when it already contains every cached row", () => {
    const sent = message("m1", 20);
    const incoming = page([sent]);
    expect(mergeMessagePage(page([sent]), incoming)).toBe(incoming);
  });
});
