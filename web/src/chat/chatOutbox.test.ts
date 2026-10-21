import { QueryClient } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as ChatApi from "./chatApi";
import { teamChatQueryKeys, type MessagePageResponse } from "./chatApi";
import { sendChatMessage, useChatOutbox } from "./chatOutbox";

const postMessageMock = vi.hoisted(() => vi.fn());

vi.mock("./chatApi", async (importOriginal) => ({
  ...(await importOriginal<typeof ChatApi>()),
  postMessage: (...args: unknown[]) => postMessageMock(...args),
}));

beforeEach(() => {
  postMessageMock.mockReset();
  useChatOutbox.setState({ entries: [] });
});

describe("sendChatMessage", () => {
  it.each([
    ["empty", ""],
    ["spaces", "   "],
    ["newlines and tabs", "\n\t \n"],
  ])("AC-015a does not post a blank or whitespace body (%s)", (_name, body) => {
    const sent = sendChatMessage(new QueryClient(), {
      channelId: "c-general",
      threadRootId: null,
      authorId: "anna",
      body,
    });
    expect(sent).toBe(false);
    expect(postMessageMock).not.toHaveBeenCalled();
    expect(useChatOutbox.getState().entries).toEqual([]);
  });

  it("AC-021a keeps the sent row when an older empty page resolves afterwards", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const key = teamChatQueryKeys.messages("dm-1");
    const empty: MessagePageResponse = {
      items: [],
      has_older: false,
      has_more: false,
      next_after: null,
      next_before: null,
    };
    let resolveEmpty: (page: MessagePageResponse) => void = () => {};
    const inflight = client
      .fetchQuery({
        queryKey: key,
        queryFn: () =>
          new Promise<MessagePageResponse>((resolve) => {
            resolveEmpty = resolve;
          }),
      })
      .catch(() => undefined);
    postMessageMock.mockResolvedValue({
      id: "m1",
      channel_id: "dm-1",
      thread_root_id: null,
      author_id: "anna",
      author_kind: "user",
      created_at: 10,
      body: "Hallo Jens, kurze Frage",
      client_id: "web-1",
    });
    sendChatMessage(client, {
      channelId: "dm-1",
      threadRootId: null,
      authorId: "anna",
      body: "Hallo Jens, kurze Frage",
    });
    await vi.waitFor(() => expect(useChatOutbox.getState().entries).toEqual([]));
    expect(client.getQueryData<MessagePageResponse>(key)?.items.map((item) => item.id)).toEqual([
      "m1",
    ]);

    resolveEmpty(empty);
    await inflight.catch(() => undefined);
    await Promise.resolve();
    expect(client.getQueryData<MessagePageResponse>(key)?.items.map((item) => item.id)).toEqual([
      "m1",
    ]);
  });
});
