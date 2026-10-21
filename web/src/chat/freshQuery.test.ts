import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { fetchLatest } from "./freshQuery";

describe("fetchLatest", () => {
  it("keeps the later page when an older in-flight response resolves afterwards", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const queryKey = ["team-chat", "messages", "d-new"] as const;
    let resolveStale: (value: string) => void = () => {};
    const stale = new Promise<string>((resolve) => {
      resolveStale = resolve;
    });
    const first = client.fetchQuery({ queryKey, queryFn: () => stale });
    const second = fetchLatest(client, queryKey, async () => "fresh");

    await expect(second).resolves.toBe("fresh");
    expect(client.getQueryData(queryKey)).toBe("fresh");

    resolveStale("stale");
    await first.catch(() => undefined);
    await Promise.resolve();
    expect(client.getQueryData(queryKey)).toBe("fresh");
  });
});
