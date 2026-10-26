import { act, cleanup, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatChangeFrame } from "@/lib/sessionUpdatesSocket";

import { teamChatQueryKeys, type BootstrapResponse, type ChatChannel } from "./chatApi";
import {
  needsYouQueryKey,
  useNeedsYouNotifications,
  type NeedsYouMention,
  type NeedsYouResponse,
} from "./NeedsYouView";
import { applyChatFrame, useTeamChatStore } from "./teamChatStore";

const fetchMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => fetchMock(...args),
}));

const ANNA = "anna@bloopark.de";
const JENS = "jens@bloopark.de";

interface FakeNotification {
  title: string;
  options?: NotificationOptions;
  onclick: (() => void) | null;
  close: () => void;
}

let shown: FakeNotification[] = [];
let requestPermission: ReturnType<typeof vi.fn>;
let needsYou: NeedsYouResponse = { mentions: [], approvals: [] };

function installNotification(permission: NotificationPermission): void {
  const ctor = vi.fn(function (
    this: FakeNotification,
    title: string,
    options?: NotificationOptions,
  ) {
    this.title = title;
    this.options = options;
    this.onclick = null;
    this.close = vi.fn();
    shown.push(this);
  }) as unknown as typeof Notification;
  requestPermission = vi.fn().mockResolvedValue(permission);
  Object.assign(ctor, { permission, requestPermission });
  vi.stubGlobal("Notification", ctor);
}

function channel(overrides: Partial<ChatChannel> & Pick<ChatChannel, "id" | "name">): ChatChannel {
  return {
    kind: "channel",
    topic: null,
    project_name: null,
    visibility: "public",
    created_by: ANNA,
    created_at: 1,
    unread_count: 0,
    mention_count: 0,
    ...overrides,
  };
}

const BOOTSTRAP: BootstrapResponse = {
  me: ANNA,
  channels: [channel({ id: "c-billing", name: "billing" })],
  dms: [channel({ id: "d-jens", kind: "dm", name: null, visibility: "private" })],
  directory: [{ id: JENS, kind: "human", display_name: "Jens" }],
  changes_cursor: `1:${"0".repeat(32)}`,
};

function billingMention(): NeedsYouMention {
  return {
    channel_id: "c-billing",
    channel_kind: "channel",
    channel_name: "billing",
    thread_root_id: null,
    message_id: "m-mention",
    author_id: JENS,
    author_label: "Jens",
    created_at: 1_700_000_000_000_000,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function Harness() {
  useNeedsYouNotifications(true);
  return null;
}

function renderHook(pathname: string): QueryClient {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[pathname]}>
        <Harness />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return queryClient;
}

/** Wait until the first (seeding) needs-you and bootstrap answers are cached. */
async function waitForSeed(queryClient: QueryClient): Promise<void> {
  await waitFor(() => {
    expect(queryClient.getQueryData(needsYouQueryKey)).toBeDefined();
    expect(queryClient.getQueryData(teamChatQueryKeys.bootstrap)).toBeDefined();
  });
}

/** Serve one new mention and wait until the hook has seen it. */
async function deliverMention(queryClient: QueryClient): Promise<void> {
  needsYou = { mentions: [billingMention()], approvals: [] };
  await act(async () => {
    await queryClient.invalidateQueries({ queryKey: needsYouQueryKey });
  });
  await waitFor(() => {
    const data = queryClient.getQueryData<NeedsYouResponse>(needsYouQueryKey);
    expect(data?.mentions).toHaveLength(1);
  });
}

beforeEach(() => {
  shown = [];
  needsYou = { mentions: [], approvals: [] };
  useTeamChatStore.setState({ changesCursor: null, changes: {}, pendingChannels: [] });
  fetchMock.mockImplementation(async (url: string) => {
    if (url === "/v1/chat/needs-you") return jsonResponse(needsYou);
    if (url === "/v1/chat/bootstrap") return jsonResponse(BOOTSTRAP);
    return jsonResponse({}, 404);
  });
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  fetchMock.mockReset();
});

describe("useNeedsYouNotifications", () => {
  it("AC-011a: a DM from Jens notifies as 'Neue Direktnachricht von Jens' without the text", async () => {
    installNotification("granted");
    const queryClient = renderHook("/");
    await waitForSeed(queryClient);

    const frame = {
      type: "chat",
      kind: "message.created",
      channel_id: "d-jens",
      message_id: "m-dm",
      thread_root_id: null,
      created_at: 1_700_000_000_000_001,
      author_id: JENS,
      body: "Die Rechnung 4711 ist raus",
    } as ChatChangeFrame;
    act(() => applyChatFrame(frame));

    expect(shown).toHaveLength(1);
    expect(shown[0].options?.body).toBe("Neue Direktnachricht von Jens");
    expect(JSON.stringify(shown[0])).not.toContain("Rechnung 4711");
  });

  it("AC-011a: a new mention notifies on a page other than /inbox (H-1)", async () => {
    installNotification("granted");
    const queryClient = renderHook("/sessions/s-1");
    await waitForSeed(queryClient);

    await deliverMention(queryClient);

    await waitFor(() => expect(shown).toHaveLength(1));
    expect(shown[0].options?.body).toBe("Neue Erwähnung in #billing");
  });

  it("AC-011a: a focused window on the mentioned channel does not notify", async () => {
    installNotification("granted");
    const queryClient = renderHook("/chat/c-billing");
    await waitForSeed(queryClient);

    await deliverMention(queryClient);

    expect(shown).toHaveLength(0);
  });

  it("AC-012a: with permission denied a new mention never calls requestPermission", async () => {
    installNotification("denied");
    const queryClient = renderHook("/sessions/s-1");
    await waitForSeed(queryClient);

    await deliverMention(queryClient);

    expect(requestPermission).not.toHaveBeenCalled();
    expect(shown).toHaveLength(0);
  });
});
