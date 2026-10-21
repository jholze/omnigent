// ThreadPane and ChannelView with an Agent-Thread: chips are mounted, agent
// Markdown goes through AI Elements, human bodies stay text.
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type * as IdentityModule from "@/lib/identity";
import type * as ChatStoreModule from "@/store/chatStore";

const fetchMock = vi.hoisted(() => vi.fn());
const sessionResult = vi.hoisted(() => ({
  current: { session: null, isLoading: false, error: null } as {
    session: { permissionLevel: number | null } | null;
    isLoading: boolean;
    error: Error | null;
  },
}));

vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof IdentityModule>()),
  authenticatedFetch: (...args: unknown[]) => fetchMock(...args),
}));
vi.mock("@/hooks/useSession", () => ({ useSession: () => sessionResult.current }));
vi.mock("@/store/chatStore", async (importOriginal) => ({
  ...(await importOriginal<typeof ChatStoreModule>()),
  ensureConversationStreamed: vi.fn(async () => {}),
  releaseConversation: vi.fn(),
}));

import { ApiError } from "@/lib/sessionsApi";
import type { ChatMessage, DirectoryEntry } from "../chatApi";
import { ChannelView } from "../ChannelView";
import { CHAT_TEST_IDS } from "../testids";
import { ThreadPane } from "../ThreadPane";
import { useAgentThreadStore } from "./agentThreadStore";

const directory = new Map<string, DirectoryEntry>([
  ["anna", { id: "anna", kind: "user", display_name: "Anna" }],
  ["bob", { id: "bob", kind: "user", display_name: "Bob" }],
  ["agent", { id: "agent", kind: "omnigent_agent", display_name: "coder" }],
]);

function message(overrides: Partial<ChatMessage> & Pick<ChatMessage, "id" | "body">): ChatMessage {
  return {
    channel_id: "c-billing",
    thread_root_id: null,
    author_id: "anna",
    author_kind: "user",
    created_at: 1_760_000_000_000_000,
    client_id: null,
    ...overrides,
  };
}

const root = message({
  id: "m-root",
  body: "<@omnigent_agent:agent> **bitte** Tests laufen lassen",
});
const replies = [
  message({
    id: "m-notice",
    thread_root_id: "m-root",
    author_id: "system",
    author_kind: "system",
    body: "Live-Ansicht nur für Anna (Freigabe am Server deaktiviert)",
  }),
  message({
    id: "m-agent",
    thread_root_id: "m-root",
    author_id: "agent",
    author_kind: "omnigent_agent",
    body: "Tests sind **grün**",
  }),
];

function page(items: ChatMessage[]) {
  return { items, has_older: false, has_more: false, next_after: null, next_before: null };
}

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function renderWith(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TooltipProvider>{children}</TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string) => {
    const parsed = new URL(url, "http://test");
    const thread = parsed.searchParams.get("thread_root_id");
    if (parsed.pathname === "/v1/chat/channels/c-billing/messages") {
      return jsonResponse(page(thread === "m-root" ? replies : [root]));
    }
    return jsonResponse({}, 404);
  });
  useAgentThreadStore.setState({
    threads: {
      "m-root": {
        channelId: "c-billing",
        rootId: "m-root",
        agentState: "active",
        sessionId: "conv_agent",
      },
    },
    sessions: {},
    watchers: {},
  });
});

afterEach(cleanup);

describe("Agent-Thread in ThreadPane", () => {
  it("AC-045b Bob without a grant sees the thread state, the stored notice and the mirrored reply", async () => {
    sessionResult.current = {
      session: null,
      isLoading: false,
      error: new ApiError("forbidden", 403, null),
    };
    renderWith(
      <ThreadPane
        channelId="c-billing"
        threadRootId="m-root"
        channelLabel="#billing"
        me="bob"
        directory={directory}
      />,
    );
    const pane = screen.getByTestId(CHAT_TEST_IDS.threadPane);
    const chip = within(within(pane).getByRole("status")).getByTestId(
      CHAT_TEST_IDS.agentStatusChip,
    );
    expect(chip.textContent).toBe("aktiv");
    expect(chip).toHaveAttribute("data-source", "thread");
    await waitFor(() =>
      expect(within(pane).getByText(/Live-Ansicht nur für Anna/)).toBeInTheDocument(),
    );
    // The stored server line is the hint; the view does not add a second one.
    expect(within(pane).getAllByText(/Live-Ansicht nur für/)).toHaveLength(1);
    // Mirrored agent reply: Markdown through AI Elements.
    const strong = await within(pane).findByText("grün");
    expect(strong).toHaveAttribute("data-streamdown", "strong");
    expect(within(pane).queryByText(/\*\*grün\*\*/)).toBeNull();
    // The watch-set holds the open thread's session.
    expect(useAgentThreadStore.getState().watchers["thread:m-root"]).toEqual(["conv_agent"]);
  });

  it("a human root keeps Markdown as text", async () => {
    sessionResult.current = { session: { permissionLevel: 1 }, isLoading: false, error: null };
    renderWith(
      <ThreadPane
        channelId="c-billing"
        threadRootId="m-root"
        channelLabel="#billing"
        me="bob"
        directory={directory}
      />,
    );
    expect(await screen.findByText(/\*\*bitte\*\* Tests laufen lassen/)).toBeInTheDocument();
  });
});

describe("Agent-Thread root in ChannelView", () => {
  it("FR-047 the root row carries the compact chip and joins the watch-set", async () => {
    useAgentThreadStore.setState({
      sessions: { conv_agent: { status: "running", pendingElicitations: 1, permissionLevel: 1 } },
    });
    renderWith(
      <ChannelView channelId="c-billing" label="#billing" me="jens" directory={directory} />,
    );
    // The open-request strip names the thread too; read the row in the list.
    const list = await screen.findByTestId(CHAT_TEST_IDS.messageList);
    const row = await within(list).findByText(/Tests laufen lassen/);
    const item = row.closest("li") as HTMLElement;
    expect(within(item).getByTestId(CHAT_TEST_IDS.agentStatusChip).textContent).toBe(
      "braucht Antwort",
    );
    expect(within(item).getByText("Agent-Thread:")).toHaveClass("sr-only");
    expect(useAgentThreadStore.getState().watchers["channel:c-billing"]).toEqual(["conv_agent"]);
  });

  it("AC-047b a reload without a grant shows the stored agent_state of the root", async () => {
    useAgentThreadStore.setState({ threads: {}, sessions: {}, watchers: {} });
    fetchMock.mockImplementation(async (url: string) => {
      const parsed = new URL(url, "http://test");
      if (parsed.pathname === "/v1/chat/channels/c-billing/messages") {
        return jsonResponse(page([{ ...root, agent_state: "start_failed" }]));
      }
      return jsonResponse({}, 404);
    });
    renderWith(
      <ChannelView channelId="c-billing" label="#billing" me="bob" directory={directory} />,
    );
    const row = await screen.findByText(/Tests laufen lassen/);
    const item = row.closest("li") as HTMLElement;
    const chip = await within(item).findByTestId(CHAT_TEST_IDS.agentStatusChip);
    expect(chip.textContent).toBe("Start fehlgeschlagen");
    expect(chip).toHaveAttribute("data-source", "thread");
    expect(useAgentThreadStore.getState().threads["m-root"].sessionId).toBeNull();
  });
});
