import {
  act,
  cleanup,
  configure,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { BootstrapResponse, ChatChannel, ChatMessage } from "./chatApi";
import { CLIENT_ID_PATTERN } from "./chatApi";
import { useChatOutbox } from "./chatOutbox";
import { threadNameOf } from "./agent/PendingAgentRequests";
import { pendingRequestCandidates, threadPath } from "./ChannelView";
import { ChatPage } from "./ChatPage";
import { CHAT_TEST_IDS } from "./testids";
import { applyChatFrame, useTeamChatStore } from "./teamChatStore";

const fetchMock = vi.hoisted(() => vi.fn());
// Generous waits: the first render imports the composer and can be slow on a busy machine.
const SLOW = { timeout: 5000 };
configure({ asyncUtilTimeout: SLOW.timeout });

vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => fetchMock(...args),
}));

vi.mock("@/lib/CapabilitiesContext", () => ({
  useServerInfo: () => ({ features: { team_chat: true } }),
}));

function channel(overrides: Partial<ChatChannel> & Pick<ChatChannel, "id">): ChatChannel {
  return {
    kind: "channel",
    name: "general",
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

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

interface FakeServer {
  bootstrap: BootstrapResponse;
  messages: ChatMessage[];
  posts: { channelId: string; body: Record<string, string> }[];
  postStatuses: (number | "network")[];
  dmPosts: unknown[];
}

let server: FakeServer;
let clock = 1_760_000_000_000_000;

function message(overrides: Partial<ChatMessage> & Pick<ChatMessage, "id" | "body">): ChatMessage {
  clock += 1000;
  return {
    channel_id: "c-general",
    thread_root_id: null,
    author_id: "anna",
    author_kind: "user",
    created_at: clock,
    client_id: null,
    ...overrides,
  };
}

function page(items: ChatMessage[]) {
  return { items, has_older: false, has_more: false, next_after: null, next_before: null };
}

function handle(url: string, init?: RequestInit) {
  const parsed = new URL(url, "http://test");
  const path = parsed.pathname;
  if (path === "/v1/chat/bootstrap") return jsonResponse(server.bootstrap);
  if (path === "/v1/chat/dms" && init?.method === "POST") {
    server.dmPosts.push(JSON.parse(String(init.body)));
    const dm = channel({ id: "d-1", kind: "dm", name: null });
    if (!server.bootstrap.dms.some((item) => item.id === dm.id)) {
      server.bootstrap = { ...server.bootstrap, dms: [...server.bootstrap.dms, dm] };
    }
    return jsonResponse(dm, 201);
  }
  const members = /^\/v1\/chat\/channels\/([^/]+)\/members$/.exec(path);
  if (members) {
    const channelId = decodeURIComponent(members[1]);
    if (channelId === "d-1") {
      return jsonResponse([{ principal_id: "jens" }, { principal_id: "anna" }]);
    }
    return jsonResponse([]);
  }
  const match = /^\/v1\/chat\/channels\/([^/]+)\/messages$/.exec(path);
  if (match) {
    const channelId = decodeURIComponent(match[1]);
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as Record<string, string>;
      server.posts.push({ channelId, body });
      const status = server.postStatuses.shift() ?? 201;
      if (status === "network") throw new TypeError("Failed to fetch");
      if (status >= 400) return jsonResponse({ detail: "down" }, status);
      const replay = server.messages.find((item) => item.client_id === body.client_id);
      if (replay) return jsonResponse(replay, 200);
      const stored = message({
        id: `m-${server.messages.length + 1}`,
        channel_id: channelId,
        body: body.body,
        author_id: server.bootstrap.me,
        client_id: body.client_id ?? null,
        thread_root_id: body.thread_root_id ?? null,
      });
      server.messages.push(stored);
      return jsonResponse(stored, 201);
    }
    const root = parsed.searchParams.get("thread_root_id");
    return jsonResponse(
      page(
        server.messages.filter(
          (item) => item.channel_id === channelId && item.thread_root_id === root,
        ),
      ),
    );
  }
  return jsonResponse({ detail: "not found" }, 404);
}

function renderAt(path: string) {
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

function composerInput() {
  return screen.getAllByTestId(CHAT_TEST_IDS.composerInput)[0] as HTMLTextAreaElement;
}

function messagePosts() {
  return server.posts;
}

beforeEach(() => {
  server = {
    bootstrap: {
      me: "jens",
      channels: [channel({ id: "c-general" })],
      dms: [],
      directory: [
        { id: "anna", kind: "user", display_name: "Anna" },
        { id: "jens", kind: "user" },
      ],
      changes_cursor: `1:${"0".repeat(32)}`,
    },
    messages: [],
    posts: [],
    postStatuses: [],
    dmPosts: [],
  };
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: unknown, init?: RequestInit) =>
    handle(String(url), init),
  );
  useTeamChatStore.setState({ changesCursor: null, changes: {}, pendingChannels: [] });
  useChatOutbox.setState({ entries: [] });
});

afterEach(() => cleanup());

describe("ChannelView", () => {
  it("shows Noch keine Nachrichten and the composer card in an empty channel", async () => {
    renderAt("/chat/c-general");
    expect(await screen.findByText("Noch keine Nachrichten", undefined, SLOW)).toBeInTheDocument();
    expect(screen.getByTestId(CHAT_TEST_IDS.composer)).toHaveAttribute("data-composer-card");
    expect(composerInput()).toHaveAccessibleName("Nachricht an #general");
  });

  it("NFR-011 Enter sends the trimmed body with a client_id, Shift+Enter does not send", async () => {
    renderAt("/chat/c-general");
    await screen.findByText("Noch keine Nachrichten", undefined, SLOW);
    const input = composerInput();

    fireEvent.change(input, { target: { value: "  Zeile eins  " } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(messagePosts()).toHaveLength(0);

    fireEvent.keyDown(input, { key: "Enter" });
    // Optimistic row before the server answers (NFR-002).
    expect(screen.getByTestId(CHAT_TEST_IDS.message)).toHaveTextContent("Zeile eins");
    expect(input.value).toBe("");
    await waitFor(() => expect(messagePosts()).toHaveLength(1));
    const [post] = messagePosts();
    expect(post.body.body).toBe("Zeile eins");
    expect(post.body.client_id).toMatch(CLIENT_ID_PATTERN);
    await waitFor(() =>
      expect(screen.getByTestId(CHAT_TEST_IDS.message)).toHaveAttribute("data-status", "sent"),
    );
  });

  it("AC-015a the composer does not post a whitespace body", async () => {
    renderAt("/chat/c-general");
    await screen.findByText("Noch keine Nachrichten", undefined, SLOW);
    fireEvent.change(composerInput(), { target: { value: "   " } });
    fireEvent.keyDown(composerInput(), { key: "Enter" });
    expect(screen.getByRole("button", { name: "Senden" })).toBeDisabled();
    await Promise.resolve();
    expect(messagePosts()).toHaveLength(0);
    expect(screen.queryByTestId(CHAT_TEST_IDS.message)).toBeNull();
  });

  it("AC-084a a 503 keeps nicht gesendet; erneut senden reuses the client_id and stores one message", async () => {
    server.postStatuses = [503];
    renderAt("/chat/c-general");
    await screen.findByText("Noch keine Nachrichten", undefined, SLOW);
    fireEvent.change(composerInput(), { target: { value: "Hallo" } });
    fireEvent.keyDown(composerInput(), { key: "Enter" });

    expect(await screen.findByTestId(CHAT_TEST_IDS.sendFailed, undefined, SLOW)).toHaveTextContent(
      "nicht gesendet",
    );
    // The composer stays usable after the failure.
    expect(composerInput()).toBeEnabled();
    fireEvent.change(composerInput(), { target: { value: "Entwurf" } });
    expect(composerInput().value).toBe("Entwurf");

    fireEvent.click(screen.getByRole("button", { name: "erneut senden" }));
    await waitFor(() => expect(server.messages).toHaveLength(1));
    expect(messagePosts()).toHaveLength(2);
    expect(messagePosts()[1].body).toEqual(messagePosts()[0].body);
    await waitFor(() => expect(screen.queryByTestId(CHAT_TEST_IDS.sendFailed)).toBeNull());
    expect(screen.getAllByTestId(CHAT_TEST_IDS.message)).toHaveLength(1);
  });

  it("a network error also keeps the row for a retry", async () => {
    server.postStatuses = ["network"];
    renderAt("/chat/c-general");
    await screen.findByText("Noch keine Nachrichten", undefined, SLOW);
    fireEvent.change(composerInput(), { target: { value: "Hallo" } });
    fireEvent.keyDown(composerInput(), { key: "Enter" });
    expect(await screen.findByRole("button", { name: "erneut senden" }, SLOW)).toBeInTheDocument();
  });

  it("a message.created frame refetches the list; the row shows author and time", async () => {
    renderAt("/chat/c-general");
    await screen.findByText("Noch keine Nachrichten", undefined, SLOW);
    const stored = message({
      id: "m-anna",
      body: "Deploy heute 16 Uhr?",
      created_at: Date.now() * 1000,
    });
    server.messages.push(stored);
    act(() => {
      applyChatFrame({
        type: "chat",
        kind: "message.created",
        channel_id: "c-general",
        message_id: stored.id,
        thread_root_id: null,
        created_at: stored.created_at,
        author_id: "anna",
      });
    });
    const row = await screen.findByTestId(CHAT_TEST_IDS.message, undefined, SLOW);
    expect(row).toHaveTextContent("Deploy heute 16 Uhr?");
    expect(within(row).getByText("Anna")).toBeInTheDocument();
    expect(row.querySelector("time")?.textContent).toMatch(/^\d{2}:\d{2}$/);
  });
});

describe("ThreadPane", () => {
  it("shows the root and the reply, and the root row counts 1 Antwort", async () => {
    const root = message({ id: "m1", body: "Release heute?" });
    server.messages.push(
      root,
      message({ id: "r1", body: "Passt", thread_root_id: "m1", author_id: "jens" }),
    );
    renderAt("/chat/c-general/t/m1");

    const pane = await screen.findByTestId(CHAT_TEST_IDS.threadPane, undefined, SLOW);
    expect(await within(pane).findByText("Passt", undefined, SLOW)).toBeInTheDocument();
    expect(within(pane).getByText("Release heute?")).toBeInTheDocument();
    expect(within(pane).getByText("1 Antwort")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId(CHAT_TEST_IDS.replyCount)).toHaveTextContent("1 Antwort"),
    );
    const back = within(pane).getByTestId(CHAT_TEST_IDS.threadBack);
    expect(back).toHaveAttribute("href", "/chat/c-general");
    expect(back).toHaveClass("min-h-11", "md:hidden");
  });

  it("a reply frame raises the live count on the root row", async () => {
    server.messages.push(message({ id: "m1", body: "Release heute?" }));
    renderAt("/chat/c-general");
    await screen.findByText("Release heute?", undefined, SLOW);
    expect(screen.queryByTestId(CHAT_TEST_IDS.replyCount)).toBeNull();
    act(() => {
      applyChatFrame({
        type: "chat",
        kind: "message.created",
        channel_id: "c-general",
        message_id: "r9",
        thread_root_id: "m1",
        created_at: clock + 1,
        author_id: "anna",
      });
    });
    expect(await screen.findByTestId(CHAT_TEST_IDS.replyCount, undefined, SLOW)).toHaveTextContent(
      "1 Antwort",
    );
  });

  it("a thread reply posts thread_root_id", async () => {
    server.messages.push(message({ id: "m1", body: "Release heute?" }));
    renderAt("/chat/c-general/t/m1");
    const pane = await screen.findByTestId(CHAT_TEST_IDS.threadPane, undefined, SLOW);
    await within(pane).findByText("Noch keine Antworten", undefined, SLOW);
    const input = within(pane).getByTestId(CHAT_TEST_IDS.composerInput);
    fireEvent.change(input, { target: { value: "Passt" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(messagePosts()).toHaveLength(1));
    expect(messagePosts()[0].body.thread_root_id).toBe("m1");
    expect(await within(pane).findByText("1 Antwort", undefined, SLOW)).toBeInTheDocument();
  });
});

describe("NewDmDialog", () => {
  it("opens a DM with the other principal and navigates to it", async () => {
    renderAt("/chat");
    fireEvent.click(await screen.findByTestId(CHAT_TEST_IDS.newDm, undefined, SLOW));
    const dialog = await screen.findByTestId(CHAT_TEST_IDS.newDmDialog, undefined, SLOW);
    const people = within(dialog).getAllByTestId(CHAT_TEST_IDS.newDmPerson);
    expect(people.map((person) => person.getAttribute("data-principal-id"))).toEqual(["anna"]);
    fireEvent.click(people[0]);

    expect(await screen.findByRole("heading", { level: 1, name: "Anna" }, SLOW)).toBeVisible();
    expect(screen.getByText("Teilnehmer: jens (du), Anna")).toBeVisible();
    expect(server.dmPosts).toEqual([{ principals: ["anna"] }]);
    const dms = screen.getByRole("region", { name: "Direktnachrichten" });
    expect(within(dms).getByRole("link", { name: "Anna" })).toBeVisible();
  });
});

describe("pendingRequestCandidates", () => {
  it("AC-107b includes a listed root that is not in the loaded tail", () => {
    const tail = [message({ id: "m-new", body: "newer" })];

    const roots = pendingRequestCandidates(
      "c-general",
      [{ rootId: "m-old", body: "Old agent question" }],
      tail,
      new Set(),
    );

    expect(roots).toEqual([
      { rootId: "m-old", body: "Old agent question", href: threadPath("c-general", "m-old") },
    ]);
  });

  it("AC-107b a listed root without a body gets an empty body for the Agent-Thread fallback", () => {
    const roots = pendingRequestCandidates(
      "c-general",
      [{ rootId: "m-deleted", body: null }],
      [],
      new Set(),
    );

    expect(roots.map((root) => root.body)).toEqual([""]);
  });

  it("AC-107b the strip titles an empty or whitespace body Agent-Thread", () => {
    expect(threadNameOf("", new Map())).toBe("Agent-Thread");
    expect(threadNameOf("  \n ", new Map())).toBe("Agent-Thread");
  });

  it("AC-107b a tail agent root is a candidate before the list returns", () => {
    const tail = [
      message({ id: "m-agent", body: "@agent hilf" }),
      message({ id: "m-plain", body: "hi" }),
    ];

    const roots = pendingRequestCandidates("c-general", [], tail, new Set(["m-agent"]));

    expect(roots.map((root) => [root.rootId, root.body])).toEqual([["m-agent", "@agent hilf"]]);
  });

  it("AC-107b a root that is both listed and in the tail appears once", () => {
    const tail = [message({ id: "m-agent", body: "tail text" })];

    const roots = pendingRequestCandidates(
      "c-general",
      [
        { rootId: "m-agent", body: "listed text" },
        { rootId: "m-agent", body: "listed text" },
      ],
      tail,
      new Set(["m-agent"]),
    );

    expect(roots).toEqual([
      { rootId: "m-agent", body: "tail text", href: threadPath("c-general", "m-agent") },
    ]);
  });
});
