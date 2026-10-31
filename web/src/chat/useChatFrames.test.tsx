import { createElement, type ReactNode } from "react";
import { QueryClient, QueryClientProvider, QueryObserver } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { BootstrapResponse, ChatChannel, ChatMessage, MessagePageResponse } from "./chatApi";
import { teamChatQueryKeys } from "./chatApi";
import { applyChatFrame, type TeamChatChange, useTeamChatStore } from "./teamChatStore";
import {
  acceptChatFrame,
  backfillFromCursor,
  bootstrapCursorTime,
  changeMovesBadges,
  countsAfterCursor,
  mentionsPrincipal,
  parseChatFrame,
  useChatFrames,
  watchChatSocketReconnect,
  withChannelCounts,
} from "./useChatFrames";

const fetchMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => fetchMock(...args),
}));

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

function messageChange(id: string, createdAt: number) {
  return {
    kind: "message",
    id,
    channel_id: "c1",
    thread_root_id: null,
    created_at: createdAt,
  };
}

function queryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
}

function messagesOf(id: string) {
  return Object.values(useTeamChatStore.getState().changes).filter(
    (change) => change.kind === "message" && change.id === id,
  );
}

beforeEach(() => {
  fetchMock.mockReset();
  useTeamChatStore.setState({ changesCursor: null, changes: {}, pendingChannels: [] });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("parseChatFrame", () => {
  it("parses a message.created frame and drops a body", () => {
    const parsed = parseChatFrame({
      type: "chat",
      kind: "message.created",
      channel_id: "c1",
      message_id: "m1",
      thread_root_id: null,
      created_at: 10,
      author_id: "anna",
      body: "do not keep",
    });
    expect(parsed).toEqual({
      type: "chat",
      kind: "message.created",
      channel_id: "c1",
      message_id: "m1",
      thread_root_id: null,
      created_at: 10,
      author_id: "anna",
    });
    expect(parsed !== null && "body" in parsed).toBe(false);
  });

  it("parses a read.updated frame", () => {
    expect(
      parseChatFrame({
        type: "chat",
        kind: "read.updated",
        channel_id: "c1",
        thread_root_id: "",
        created_at: 4,
        id: "m1",
      }),
    ).toEqual({
      type: "chat",
      kind: "read.updated",
      channel_id: "c1",
      thread_root_id: "",
      created_at: 4,
      id: "m1",
    });
  });

  it("ignores unknown chat kinds and non-chat frames", () => {
    expect(parseChatFrame({ type: "chat", kind: "typing", channel_id: "c1" })).toBeNull();
    expect(parseChatFrame({ type: "heartbeat" })).toBeNull();
    expect(parseChatFrame(null)).toBeNull();
    expect(
      parseChatFrame({
        type: "chat",
        kind: "message.created",
        channel_id: "c1",
        thread_root_id: null,
        created_at: 1,
        author_id: "anna",
      }),
    ).toBeNull();
  });
});

describe("backfill", () => {
  it("AC-082b backfill merge dedupes a message already applied from a frame", async () => {
    applyChatFrame({
      type: "chat",
      kind: "message.created",
      channel_id: "c1",
      message_id: "m1",
      thread_root_id: null,
      created_at: 10,
      author_id: "anna",
    });
    let calls = 0;
    fetchMock.mockImplementation(async (url: string) => {
      if (!url.startsWith("/v1/chat/changes")) throw new Error(url);
      calls += 1;
      if (calls === 1) {
        return jsonResponse({
          items: [messageChange("m1", 10), messageChange("m2", 11)],
          next: "11:m2",
          reset: false,
        });
      }
      return jsonResponse({ items: [], next: null, reset: false });
    });

    await backfillFromCursor(queryClient(), "1:m0");

    expect(messagesOf("m1")).toHaveLength(1);
    expect(messagesOf("m2")).toHaveLength(1);
    expect(useTeamChatStore.getState().changesCursor).toBe("11:m2");
  });

  it("refetches bootstrap and stores its cursor when the changes feed sets reset", async () => {
    const cursor = `50:${"0".repeat(32)}`;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/v1/chat/changes")) {
        return jsonResponse({
          items: [messageChange("m9", 9)],
          next: "9:m9",
          reset: true,
        });
      }
      if (url === "/v1/chat/bootstrap") {
        return jsonResponse({
          me: "anna",
          channels: [
            {
              id: "c1",
              kind: "channel",
              name: "general",
              topic: null,
              project_name: null,
              visibility: "public",
              created_by: "anna",
              created_at: 1,
              unread_count: 0,
              mention_count: 0,
            },
          ],
          dms: [],
          directory: [],
          changes_cursor: cursor,
        });
      }
      throw new Error(url);
    });

    await backfillFromCursor(queryClient(), "1:m0");

    expect(messagesOf("m9")).toHaveLength(1);
    expect(useTeamChatStore.getState().changesCursor).toBe(cursor);
    expect(useTeamChatStore.getState().pendingChannels).toContain("c1");
  });

  it("AC-083a reloads bootstrap and the open views when the changes feed sets reset", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/v1/chat/changes")) {
        return jsonResponse({ items: [], next: null, reset: true });
      }
      if (url === "/v1/chat/bootstrap") {
        return jsonResponse({
          me: "anna",
          channels: [],
          dms: [],
          directory: [],
          changes_cursor: "60:fresh",
        });
      }
      throw new Error(url);
    });
    const client = queryClient();
    const openMessages = vi.fn(async () => ({ items: [] }));
    const openView = new QueryObserver(client, {
      queryKey: teamChatQueryKeys.messages("c1"),
      queryFn: openMessages,
    });
    const stopObserving = openView.subscribe(() => {});
    await vi.waitFor(() => expect(openMessages).toHaveBeenCalledTimes(1));
    client.setQueryData(teamChatQueryKeys.thread("c1", "root-1"), { items: [] });

    await backfillFromCursor(client, "1:m0");

    expect(fetchMock).toHaveBeenCalledWith("/v1/chat/bootstrap", expect.anything());
    expect(useTeamChatStore.getState().changesCursor).toBe("60:fresh");
    expect(openMessages).toHaveBeenCalledTimes(2);
    expect(client.getQueryState(teamChatQueryKeys.thread("c1", "root-1"))?.isInvalidated).toBe(
      true,
    );
    stopObserving();
  });
});

function reconnectSocket() {
  let connected = false;
  const listeners = new Set<() => void>();
  return {
    isConnected: () => connected,
    subscribeStatus(listener: () => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    setConnected(value: boolean) {
      connected = value;
      for (const listener of listeners) listener();
    },
  };
}

function messageFrame(id: string, createdAt: number) {
  return {
    type: "chat" as const,
    kind: "message.created" as const,
    channel_id: "c1",
    message_id: id,
    thread_root_id: null,
    created_at: createdAt,
    author_id: "anna",
  };
}

describe("AC-082a", () => {
  it("AC-082a backfills three changes after reconnect before applying a newer frame", async () => {
    const cursor = "7:cursor";
    useTeamChatStore.getState().seedCursor(cursor);
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    fetchMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/v1/chat/changes")) {
        await gate;
        return jsonResponse({
          items: [messageChange("m1", 1), messageChange("m2", 2), messageChange("m3", 3)],
          next: "10:m3",
          reset: false,
        });
      }
      throw new Error(url);
    });

    const socket = reconnectSocket();
    const stop = watchChatSocketReconnect(socket, queryClient());
    socket.setConnected(true);
    socket.setConnected(false);
    socket.setConnected(true);
    acceptChatFrame(messageFrame("m2", 2));
    acceptChatFrame(messageFrame("m4", 4));

    await act(async () => {
      release();
    });

    await waitFor(() => {
      expect(messageIds()).toEqual(["m1", "m2", "m3", "m4"]);
    });
    expect(fetchMock).toHaveBeenCalledWith(
      `/v1/chat/changes?${new URLSearchParams({ after: cursor })}`,
      expect.anything(),
    );
    expect(messageIds().filter((id) => id === "m2")).toHaveLength(1);
    stop();
  });
});

function messageIds() {
  return Object.values(useTeamChatStore.getState().changes)
    .filter((change) => change.kind === "message")
    .map((change) => change.id);
}

describe("useChatFrames", () => {
  it("caches a channel message page after a message.created frame", async () => {
    const page: MessagePageResponse = {
      items: [
        {
          id: "m1",
          channel_id: "c1",
          thread_root_id: null,
          author_id: "anna",
          author_kind: "user",
          created_at: 10,
          body: "cached only",
          client_id: null,
        },
      ],
      has_older: false,
      has_more: false,
      next_after: null,
      next_before: null,
    };
    fetchMock.mockResolvedValue(jsonResponse(page));
    applyChatFrame({
      type: "chat",
      kind: "message.created",
      channel_id: "c1",
      message_id: "m1",
      thread_root_id: null,
      created_at: 10,
      author_id: "anna",
    });
    const client = queryClient();
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client }, children);

    renderHook(() => useChatFrames(true), { wrapper });

    await waitFor(() => {
      expect(
        client.getQueryData<MessagePageResponse>(teamChatQueryKeys.messages("c1"))?.items[0]?.id,
      ).toBe("m1");
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/chat/channels/c1/messages?tail=true",
      expect.anything(),
    );
  });

  it("fetches twice when a second message arrives inside the 30s staleTime", async () => {
    const page = (id: string, createdAt: number): MessagePageResponse => ({
      items: [
        {
          id,
          channel_id: "c1",
          thread_root_id: null,
          author_id: "anna",
          author_kind: "user",
          created_at: createdAt,
          body: id,
          client_id: null,
        },
      ],
      has_older: false,
      has_more: false,
      next_after: null,
      next_before: null,
    });
    let calls = 0;
    fetchMock.mockImplementation(async () => {
      calls += 1;
      return jsonResponse(calls === 1 ? page("m1", 10) : page("m2", 11));
    });
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, refetchOnWindowFocus: false, staleTime: 30_000 },
      },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client }, children);

    renderHook(() => useChatFrames(true), { wrapper });
    act(() => {
      applyChatFrame({
        type: "chat",
        kind: "message.created",
        channel_id: "c1",
        message_id: "m1",
        thread_root_id: null,
        created_at: 10,
        author_id: "anna",
      });
    });
    await waitFor(() => {
      expect(
        client.getQueryData<MessagePageResponse>(teamChatQueryKeys.messages("c1"))?.items[0]?.id,
      ).toBe("m1");
    });

    act(() => {
      applyChatFrame({
        type: "chat",
        kind: "message.created",
        channel_id: "c1",
        message_id: "m2",
        thread_root_id: null,
        created_at: 11,
        author_id: "anna",
      });
    });
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(
        client.getQueryData<MessagePageResponse>(teamChatQueryKeys.messages("c1"))?.items[0]?.id,
      ).toBe("m2");
    });
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/v1/chat/channels/c1/messages?tail=true",
      expect.anything(),
    );
  });

  it("does not fetch while disabled", async () => {
    applyChatFrame({
      type: "chat",
      kind: "message.created",
      channel_id: "c1",
      message_id: "m1",
      thread_root_id: null,
      created_at: 10,
      author_id: "anna",
    });
    const client = queryClient();
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client }, children);
    renderHook(() => useChatFrames(false), { wrapper });
    await Promise.resolve();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

function row(
  overrides: Partial<ChatMessage> & Pick<ChatMessage, "id" | "created_at">,
): ChatMessage {
  return {
    channel_id: "c1",
    thread_root_id: null,
    author_id: "jens",
    author_kind: "user",
    body: "hi",
    client_id: null,
    ...overrides,
  };
}

function badgeChannel(id: string, unread = 0, mentions = 0): ChatChannel {
  return {
    id,
    name: id,
    kind: "channel",
    topic: null,
    project_name: null,
    visibility: "public",
    created_by: "anna",
    created_at: 1,
    unread_count: unread,
    mention_count: mentions,
  };
}

function badgeBootstrap(overrides: Partial<BootstrapResponse> = {}): BootstrapResponse {
  return {
    me: "anna",
    channels: [badgeChannel("c1")],
    dms: [],
    directory: [],
    changes_cursor: `5:${"0".repeat(32)}`,
    ...overrides,
  };
}

function badgeChange(
  overrides: Partial<TeamChatChange> & Pick<TeamChatChange, "kind">,
): TeamChatChange {
  return { id: "m1", channel_id: "c1", thread_root_id: null, created_at: 10, ...overrides };
}

describe("mentionsPrincipal", () => {
  it("is true for a <@user:me> token", () => {
    expect(mentionsPrincipal("hey <@user:anna> look", "anna")).toBe(true);
  });

  it("is true for a <@agent_account:me> token", () => {
    expect(mentionsPrincipal("<@agent_account:anna> run it", "anna")).toBe(true);
  });

  it("is false for another principal's token", () => {
    expect(mentionsPrincipal("<@user:jens> and <@agent_account:annabel>", "anna")).toBe(false);
  });

  it("is false when me is empty", () => {
    expect(mentionsPrincipal("<@user:>", "")).toBe(false);
  });
});

describe("bootstrapCursorTime", () => {
  it("reads the microsecond prefix of <us>:<hex>", () => {
    expect(bootstrapCursorTime(`1700000000000000:${"a".repeat(32)}`)).toBe(1700000000000000);
  });

  it("is null when the cursor is missing", () => {
    expect(bootstrapCursorTime(undefined)).toBeNull();
  });

  it("is null when the prefix is not a positive number", () => {
    expect(bootstrapCursorTime("abc:ff")).toBeNull();
    expect(bootstrapCursorTime("0:ff")).toBeNull();
    expect(bootstrapCursorTime("-5:ff")).toBeNull();
  });
});

describe("countsAfterCursor", () => {
  const cursor = { id: "m2", created_at: 20 };

  it("counts only other people's top-level rows after the cursor", () => {
    const items = [
      row({ id: "m1", created_at: 10 }),
      row({ id: "m2", created_at: 20 }),
      row({ id: "m3", created_at: 30 }),
      row({ id: "m4", created_at: 40 }),
    ];
    expect(countsAfterCursor(items, cursor, "anna", false)).toEqual({ unread: 2, mentions: 0 });
  });

  it("skips thread replies after the cursor", () => {
    const items = [
      row({ id: "m2", created_at: 20 }),
      row({ id: "r1", created_at: 30, thread_root_id: "m2" }),
    ];
    expect(countsAfterCursor(items, cursor, "anna", false)).toEqual({ unread: 0, mentions: 0 });
  });

  it("skips my own messages after the cursor", () => {
    const items = [
      row({ id: "m2", created_at: 20 }),
      row({ id: "m3", created_at: 30, author_id: "anna" }),
    ];
    expect(countsAfterCursor(items, cursor, "anna", false)).toEqual({ unread: 0, mentions: 0 });
  });

  it("counts a row naming me twice as one mention", () => {
    const items = [
      row({ id: "m2", created_at: 20 }),
      row({ id: "m3", created_at: 30, body: "<@user:anna> <@agent_account:anna>" }),
    ];
    expect(countsAfterCursor(items, cursor, "anna", false)).toEqual({ unread: 1, mentions: 1 });
  });

  it("is null when older messages exist and the page never reaches the cursor", () => {
    const items = [row({ id: "m3", created_at: 30 }), row({ id: "m4", created_at: 40 })];
    expect(countsAfterCursor(items, cursor, "anna", true)).toBeNull();
  });
});

describe("withChannelCounts", () => {
  it("replaces one channel's counts in channels", () => {
    const data = badgeBootstrap({ channels: [badgeChannel("c1"), badgeChannel("c2", 4, 1)] });
    const next = withChannelCounts(data, "c1", () => ({ unread: 3, mentions: 2 }));
    expect(next.channels[0]).toMatchObject({ id: "c1", unread_count: 3, mention_count: 2 });
    expect(next.channels[1]).toBe(data.channels[1]);
  });

  it("replaces one channel's counts in dms", () => {
    const data = badgeBootstrap({ dms: [{ ...badgeChannel("d1", 1, 0), kind: "dm" }] });
    const next = withChannelCounts(data, "d1", (unread, mentions) => ({
      unread: unread + 1,
      mentions: mentions + 1,
    }));
    expect(next.dms[0]).toMatchObject({ id: "d1", unread_count: 2, mention_count: 1 });
  });

  it("returns the same object when nothing moved", () => {
    const data = badgeBootstrap({ channels: [badgeChannel("c1", 2, 1)] });
    expect(withChannelCounts(data, "c1", () => ({ unread: 2, mentions: 1 }))).toBe(data);
    expect(withChannelCounts(data, "missing", () => ({ unread: 9, mentions: 9 }))).toBe(data);
  });
});

describe("changeMovesBadges", () => {
  const listed = new Set(["c1"]);

  it("is false for a channel bootstrap does not list", () => {
    expect(
      changeMovesBadges(
        badgeChange({ kind: "message", channel_id: "c-new", author_id: "jens" }),
        "anna",
        listed,
      ),
    ).toBe(false);
  });

  it("is false for a thread reply", () => {
    expect(
      changeMovesBadges(
        badgeChange({ kind: "message", thread_root_id: "root", author_id: "jens" }),
        "anna",
        listed,
      ),
    ).toBe(false);
  });

  it("is false for a thread read cursor", () => {
    expect(
      changeMovesBadges(badgeChange({ kind: "read", thread_root_id: "root" }), "anna", listed),
    ).toBe(false);
  });

  it("is true for a channel-level read", () => {
    expect(
      changeMovesBadges(badgeChange({ kind: "read", thread_root_id: "" }), "anna", listed),
    ).toBe(true);
    expect(
      changeMovesBadges(badgeChange({ kind: "read", thread_root_id: null }), "anna", listed),
    ).toBe(true);
  });

  it("is true for someone else's top-level message in a listed channel", () => {
    expect(
      changeMovesBadges(badgeChange({ kind: "message", author_id: "jens" }), "anna", listed),
    ).toBe(true);
  });
});

describe("useChatFrames badge sync", () => {
  it("a foreign top-level message in a listed channel updates the cached badge without GET /v1/chat/bootstrap", async () => {
    const page: MessagePageResponse = {
      items: [row({ id: "m1", created_at: 10, body: "<@user:anna> ping" })],
      has_older: false,
      has_more: false,
      next_after: null,
      next_before: null,
    };
    fetchMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/v1/chat/channels/c1/messages")) return jsonResponse(page);
      throw new Error(url);
    });
    const client = queryClient();
    client.setQueryData(teamChatQueryKeys.bootstrap, badgeBootstrap());
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client }, children);

    renderHook(() => useChatFrames(true), { wrapper });
    act(() => {
      applyChatFrame({
        type: "chat",
        kind: "message.created",
        channel_id: "c1",
        message_id: "m1",
        thread_root_id: null,
        created_at: 10,
        author_id: "jens",
      });
    });

    await waitFor(() => {
      expect(
        client.getQueryData<BootstrapResponse>(teamChatQueryKeys.bootstrap)?.channels[0],
      ).toMatchObject({ unread_count: 1, mention_count: 1 });
    });
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls).not.toContain("/v1/chat/bootstrap");
  });

  it("a listed-channel frame that arrives before bootstrap is counted once after bootstrap is set", async () => {
    const page: MessagePageResponse = {
      items: [row({ id: "m1", created_at: 10 })],
      has_older: false,
      has_more: false,
      next_after: null,
      next_before: null,
    };
    fetchMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/v1/chat/channels/c1/messages")) return jsonResponse(page);
      throw new Error(url);
    });
    const client = queryClient();
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client }, children);
    const unreadOf = () =>
      client.getQueryData<BootstrapResponse>(teamChatQueryKeys.bootstrap)?.channels[0]
        ?.unread_count;

    renderHook(() => useChatFrames(true), { wrapper });
    act(() => {
      applyChatFrame({
        type: "chat",
        kind: "message.created",
        channel_id: "c1",
        message_id: "m1",
        thread_root_id: null,
        created_at: 10,
        author_id: "jens",
      });
    });
    await waitFor(() => {
      expect(client.getQueryData(teamChatQueryKeys.messages("c1"))).toBeDefined();
    });
    expect(client.getQueryData(teamChatQueryKeys.bootstrap)).toBeUndefined();

    // Cursor time 5 is older than the message (10), so bootstrap did not count it.
    act(() => {
      client.setQueryData(teamChatQueryKeys.bootstrap, badgeBootstrap());
    });
    await waitFor(() => expect(unreadOf()).toBe(1));

    act(() => {
      const current = client.getQueryData<BootstrapResponse>(teamChatQueryKeys.bootstrap);
      client.setQueryData(teamChatQueryKeys.bootstrap, { ...current! });
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(unreadOf()).toBe(1);
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls).not.toContain("/v1/chat/bootstrap");
  });

  it("a message at or before the bootstrap cursor does not increment a count that already includes it", async () => {
    const page: MessagePageResponse = {
      items: [row({ id: "m1", created_at: 10 }), row({ id: "m2", created_at: 11 })],
      has_older: false,
      has_more: false,
      next_after: null,
      next_before: null,
    };
    fetchMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/v1/chat/channels/c1/messages")) return jsonResponse(page);
      throw new Error(url);
    });
    const client = queryClient();
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client }, children);
    const unreadOf = () =>
      client.getQueryData<BootstrapResponse>(teamChatQueryKeys.bootstrap)?.channels[0]
        ?.unread_count;

    renderHook(() => useChatFrames(true), { wrapper });
    act(() => {
      for (const [messageId, createdAt] of [
        ["m1", 10],
        ["m2", 11],
      ] as const) {
        applyChatFrame({
          type: "chat",
          kind: "message.created",
          channel_id: "c1",
          message_id: messageId,
          thread_root_id: null,
          created_at: createdAt,
          author_id: "jens",
        });
      }
    });
    await waitFor(() => {
      expect(client.getQueryData(teamChatQueryKeys.messages("c1"))).toBeDefined();
    });

    // Bootstrap cut at m1 (10) and already counts it; m2 (11) is after the cut.
    act(() => {
      client.setQueryData(
        teamChatQueryKeys.bootstrap,
        badgeBootstrap({
          channels: [badgeChannel("c1", 1)],
          changes_cursor: `10:${"0".repeat(32)}`,
        }),
      );
    });
    await waitFor(() => expect(unreadOf()).toBe(2));
    await act(async () => {
      await Promise.resolve();
    });
    expect(unreadOf()).toBe(2);
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls).not.toContain("/v1/chat/bootstrap");
  });
});
