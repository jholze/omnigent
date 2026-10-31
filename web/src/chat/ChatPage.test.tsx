import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { teamChatQueryKeys, type BootstrapResponse, type ChatChannel } from "./chatApi";
import { ChatPage } from "./ChatPage";
import { CHAT_TEST_IDS } from "./testids";
import { applyChatFrame, useTeamChatStore } from "./teamChatStore";

const fetchMock = vi.hoisted(() => vi.fn());
const serverState = vi.hoisted(() => ({
  info: { features: { team_chat: false } } as { features: Record<string, boolean> } | "loading",
}));

vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => fetchMock(...args),
}));

vi.mock("@/lib/CapabilitiesContext", () => ({
  useServerInfo: () => serverState.info,
}));

function channel(overrides: Partial<ChatChannel> & Pick<ChatChannel, "id" | "name">): ChatChannel {
  return {
    kind: "channel",
    topic: null,
    project_name: null,
    visibility: "public",
    created_by: "anna",
    created_at: 1,
    unread_count: 0,
    mention_count: 0,
    ...overrides,
  };
}

function bootstrap(overrides: Partial<BootstrapResponse> = {}): BootstrapResponse {
  return {
    me: "anna",
    channels: [
      channel({ id: "c-general", name: "general" }),
      channel({ id: "c-ops", name: "#ops" }),
    ],
    dms: [],
    directory: [],
    changes_cursor: `1:${"0".repeat(32)}`,
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

function renderPage(path = "/chat") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/chat/:channelId" element={<ChatPage />} />
          <Route path="/chat/:channelId/t/:threadRootId" element={<ChatPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  serverState.info = { features: { team_chat: false } };
  fetchMock.mockReset();
  useTeamChatStore.setState({ changesCursor: null, changes: {}, pendingChannels: [] });
});

afterEach(() => {
  cleanup();
});

describe("ChatPage", () => {
  it("AC-001b route guard shows the disabled message and does not call /v1/chat when the flag is off", async () => {
    renderPage();
    expect(screen.getByText("Chat ist auf diesem Server nicht aktiviert")).toBeInTheDocument();
    expect(screen.queryByTestId(CHAT_TEST_IDS.page)).toBeNull();
    await Promise.resolve();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("AC-001a sidebar renders #general and an empty Direktnachrichten section from a mocked bootstrap", async () => {
    serverState.info = { features: { team_chat: true } };
    fetchMock.mockResolvedValue(jsonResponse(bootstrap()));
    renderPage();

    expect(await screen.findByRole("link", { name: "#general" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "#ops" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "##ops" })).toBeNull();
    const dms = screen.getByRole("region", { name: "Direktnachrichten" });
    expect(within(dms).getByRole("heading", { name: "Direktnachrichten" })).toBeVisible();
    expect(within(dms).queryAllByRole("link")).toHaveLength(0);
    expect(screen.getByRole("heading", { name: "Channels" })).toBeVisible();
    expect(screen.getByTestId(CHAT_TEST_IDS.page)).toBeInTheDocument();
    expect(screen.getByTestId(CHAT_TEST_IDS.sidebar)).toBeInTheDocument();
    expect(screen.queryByTestId(CHAT_TEST_IDS.message)).toBeNull();
    expect(screen.queryByTestId(CHAT_TEST_IDS.composer)).toBeNull();
    expect(screen.queryByTestId(CHAT_TEST_IDS.threadPane)).toBeNull();

    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls.length).toBeGreaterThan(0);
    expect(urls.every((url) => url === "/v1/chat/bootstrap")).toBe(true);
    expect(screen.queryByTestId(CHAT_TEST_IDS.back)).toBeNull();
  });

  it("mobile layout renders a Channels back control to /chat on /chat/:channelId", async () => {
    serverState.info = { features: { team_chat: true } };
    fetchMock.mockResolvedValue(jsonResponse(bootstrap()));
    renderPage("/chat/c-general");

    expect(await screen.findByRole("heading", { name: "#general" })).toBeInTheDocument();
    const back = screen.getByTestId(CHAT_TEST_IDS.back);
    expect(back).toBeInTheDocument();
    expect(back).toHaveAccessibleName("Channels");
    expect(back).toHaveAttribute("href", "/chat");
    expect(back).toHaveClass("lg:hidden", "min-h-11");
    expect(screen.getByTestId(CHAT_TEST_IDS.sidebar)).toHaveClass("max-lg:hidden", "lg:w-60");
  });

  it("mobile layout keeps the back control on the thread route", async () => {
    serverState.info = { features: { team_chat: true } };
    fetchMock.mockResolvedValue(jsonResponse(bootstrap()));
    renderPage("/chat/c-general/t/m-1");

    expect(await screen.findByRole("heading", { name: "#general" })).toBeInTheDocument();
    expect(screen.getByTestId(CHAT_TEST_IDS.back)).toBeInTheDocument();
  });

  it("pending bootstrap shows a busy spinner instead of empty lists", async () => {
    serverState.info = { features: { team_chat: true } };
    fetchMock.mockReturnValue(new Promise(() => {}));
    renderPage();

    const page = screen.getByTestId(CHAT_TEST_IDS.page);
    expect(page).toHaveAttribute("aria-busy", "true");
    expect(within(page).getByRole("status", { name: "Lädt…" })).toBeInTheDocument();
    expect(screen.queryByTestId(CHAT_TEST_IDS.sidebar)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Channels" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Direktnachrichten" })).toBeNull();
  });

  it("failed bootstrap shows the error and retry refetches into the channel list", async () => {
    serverState.info = { features: { team_chat: true } };
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "boom" }, 500));
    renderPage();

    const error = await screen.findByTestId(CHAT_TEST_IDS.bootstrapError);
    expect(error).toHaveTextContent("Chat konnte nicht geladen werden");
    expect(screen.queryByTestId(CHAT_TEST_IDS.sidebar)).toBeNull();
    expect(screen.getByTestId(CHAT_TEST_IDS.page)).not.toHaveAttribute("aria-busy");
    const retry = screen.getByTestId(CHAT_TEST_IDS.bootstrapRetry);
    expect(retry).toHaveAccessibleName("Erneut versuchen");
    expect(retry).toHaveClass("min-h-11");

    fetchMock.mockResolvedValue(jsonResponse(bootstrap()));
    const callsBefore = fetchMock.mock.calls.length;
    fireEvent.click(retry);
    expect(await screen.findByRole("link", { name: "#general" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.length).toBeGreaterThan(callsBefore);
    expect(screen.queryByTestId(CHAT_TEST_IDS.bootstrapError)).toBeNull();
  });

  it("unknown channel id shows Channel nicht gefunden and keeps the back link", async () => {
    serverState.info = { features: { team_chat: true } };
    fetchMock.mockResolvedValue(jsonResponse(bootstrap()));
    renderPage("/chat/c-nope");

    const missing = await screen.findByTestId(CHAT_TEST_IDS.channelMissing);
    expect(missing).toHaveTextContent("Channel nicht gefunden");
    expect(screen.getByTestId(CHAT_TEST_IDS.back)).toHaveAttribute("href", "/chat");
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
  });

  it("known channel and DM do not show the missing state", async () => {
    serverState.info = { features: { team_chat: true } };
    fetchMock.mockResolvedValue(
      jsonResponse(bootstrap({ dms: [channel({ id: "d-jens", name: "Jens", kind: "dm" })] })),
    );
    renderPage("/chat/d-jens");

    expect(await screen.findByRole("heading", { level: 1, name: "Jens" })).toBeInTheDocument();
    expect(screen.queryByTestId(CHAT_TEST_IDS.channelMissing)).toBeNull();
  });

  it("keeps an open DM mounted when a later bootstrap omits it", async () => {
    serverState.info = { features: { team_chat: true } };
    const dm = channel({ id: "d-jens", name: "Jens", kind: "dm" });
    let bootstrapCalls = 0;
    fetchMock.mockImplementation(async (input: unknown) => {
      const url = String(input);
      if (url.includes("/messages")) {
        return jsonResponse({
          items: [],
          has_older: false,
          has_more: false,
          next_after: null,
          next_before: null,
        });
      }
      // Leave members pending so the heading stays the channel name.
      if (url.includes("/members")) return new Promise(() => {});
      if (url.includes("/changes")) return jsonResponse({ items: [], next: null, reset: false });
      bootstrapCalls += 1;
      return jsonResponse(bootstrap({ dms: bootstrapCalls === 1 ? [dm] : [] }));
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/chat/d-jens"]}>
          <Routes>
            <Route path="/chat/:channelId" element={<ChatPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { level: 1, name: "Jens" })).toBeInTheDocument();
    const input = await screen.findByTestId(CHAT_TEST_IDS.composerInput);
    fireEvent.change(input, { target: { value: "Hallo" } });
    await act(async () => {
      await client.refetchQueries({ queryKey: teamChatQueryKeys.bootstrap });
    });

    await waitFor(() => {
      const cached = client.getQueryData<BootstrapResponse>(teamChatQueryKeys.bootstrap);
      expect(cached?.dms ?? []).toEqual([]);
      expect(screen.queryByRole("link", { name: "Jens" })).toBeNull();
    });
    expect(screen.getByRole("heading", { level: 1, name: "Jens" })).toBeInTheDocument();
    expect(screen.getByTestId(CHAT_TEST_IDS.composerInput)).toHaveValue("Hallo");
    expect(screen.queryByTestId(CHAT_TEST_IDS.channelMissing)).toBeNull();
  });

  it("long channel and DM names wrap instead of truncating", async () => {
    serverState.info = { features: { team_chat: true } };
    const longName = "a".repeat(120);
    fetchMock.mockResolvedValue(
      jsonResponse(
        bootstrap({
          channels: [channel({ id: "c-long", name: longName })],
          dms: [channel({ id: "d-long", name: `dm-${longName}`, kind: "dm" })],
        }),
      ),
    );
    renderPage("/chat/c-long");

    const heading = await screen.findByRole("heading", { level: 1, name: `#${longName}` });
    expect(heading).toHaveClass("min-w-0", "break-words");
    expect(heading).not.toHaveClass("truncate");
    for (const name of [`#${longName}`, `dm-${longName}`]) {
      const link = screen.getByRole("link", { name });
      expect(link).toHaveClass("min-w-0", "break-words");
      expect(link).not.toHaveClass("truncate");
    }
    expect(screen.getByTestId(CHAT_TEST_IDS.page)).toHaveClass("overflow-x-hidden");
    expect(screen.getByTestId(CHAT_TEST_IDS.sidebar)).toHaveClass("lg:w-60");
  });
});

describe("ChatPage bootstrap refetch for a new channel (AC-021a)", () => {
  const NEW_DM = channel({ id: "d-new", name: "Jens", kind: "dm" });
  // Longer than the product's retry back-off (400ms + 800ms).
  const QUIET_MS = 2_000;

  function bootstrapCalls(): number {
    return fetchMock.mock.calls.filter((call) => String(call[0]) === "/v1/chat/bootstrap").length;
  }

  function messageIn(channelId: string, id: string): void {
    act(() => {
      useTeamChatStore
        .getState()
        .mergeChanges([
          { kind: "message", id, channel_id: channelId, thread_root_id: null, created_at: 10 },
        ]);
    });
  }

  async function renderLoaded(): Promise<number> {
    renderPage();
    expect(await screen.findByRole("link", { name: "#general" })).toBeInTheDocument();
    return bootstrapCalls();
  }

  async function settle(): Promise<void> {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(QUIET_MS);
    });
  }

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serverState.info = { features: { team_chat: true } };
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("a response without the new DM leaves it open, and a later message fetches bootstrap again", async () => {
    fetchMock.mockImplementation(async () => jsonResponse(bootstrap()));
    const initial = await renderLoaded();

    messageIn("d-new", "m-1");
    await settle();
    const afterFirstRound = bootstrapCalls();
    expect(afterFirstRound).toBeGreaterThan(initial);

    messageIn("d-new", "m-2");
    await waitFor(() => expect(bootstrapCalls()).toBeGreaterThan(afterFirstRound));
  });

  it("a response that lists the new DM does not fetch again for the same changes", async () => {
    fetchMock.mockImplementation(async () => jsonResponse(bootstrap()));
    const initial = await renderLoaded();
    fetchMock.mockImplementation(async () => jsonResponse(bootstrap({ dms: [NEW_DM] })));

    messageIn("d-new", "m-1");
    await waitFor(() => expect(bootstrapCalls()).toBe(initial + 1));
    expect(await screen.findByRole("link", { name: "Jens" })).toBeInTheDocument();
    await settle();
    expect(bootstrapCalls()).toBe(initial + 1);
  });

  it("a failed fetch does not mark the channel confirmed", async () => {
    fetchMock.mockImplementation(async () => jsonResponse(bootstrap()));
    const initial = await renderLoaded();
    fetchMock.mockImplementation(async () => jsonResponse({ detail: "boom" }, 500));

    messageIn("d-new", "m-1");
    await settle();
    const afterFailedRound = bootstrapCalls();
    expect(afterFailedRound).toBeGreaterThan(initial);

    fetchMock.mockImplementation(async () => jsonResponse(bootstrap({ dms: [NEW_DM] })));
    messageIn("d-new", "m-2");
    await waitFor(() => expect(bootstrapCalls()).toBe(afterFailedRound + 1));
  });

  it("stops after three attempts until a new message change arrives", async () => {
    fetchMock.mockImplementation(async () => jsonResponse(bootstrap()));
    const initial = await renderLoaded();

    messageIn("d-new", "m-1");
    await settle();
    expect(bootstrapCalls()).toBe(initial + 3);
    await settle();
    expect(bootstrapCalls()).toBe(initial + 3);

    messageIn("d-new", "m-2");
    await settle();
    expect(bootstrapCalls()).toBe(initial + 6);
  });
});

describe("ChatPage badge for a listed channel", () => {
  beforeEach(() => {
    serverState.info = { features: { team_chat: true } };
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("a foreign top-level message in #general raises its badge without GET /v1/chat/bootstrap", async () => {
    const page = {
      items: [
        {
          id: "m-1",
          channel_id: "c-general",
          thread_root_id: null,
          author_id: "jens",
          author_kind: "user",
          created_at: 10,
          body: "<@user:anna> ping",
          client_id: null,
        },
      ],
      has_older: false,
      has_more: false,
      next_after: null,
      next_before: null,
    };
    fetchMock.mockImplementation(async (url: string) =>
      url.startsWith("/v1/chat/channels/c-general/messages")
        ? jsonResponse(page)
        : jsonResponse(bootstrap()),
    );
    const bootstrapCalls = () =>
      fetchMock.mock.calls.filter((call) => String(call[0]) === "/v1/chat/bootstrap").length;
    renderPage();
    expect(await screen.findByRole("link", { name: "#general" })).toBeInTheDocument();
    const initial = bootstrapCalls();

    act(() => {
      applyChatFrame({
        type: "chat",
        kind: "message.created",
        channel_id: "c-general",
        message_id: "m-1",
        thread_root_id: null,
        created_at: 10,
        author_id: "jens",
      });
    });

    expect(
      await screen.findByRole("link", { name: "#general, 1 ungelesen, 1 Erwähnung" }),
    ).toBeInTheDocument();
    // Give a late refetch (ChatPage retries after 400ms) time to show up.
    await act(async () => {
      await new Promise((resolve) => {
        setTimeout(resolve, 600);
      });
    });
    expect(bootstrapCalls()).toBe(initial);
  });
});
