// Agent-Thread reply delivery labels: "nicht an Agent" and the failed
// forward with its retry. ThreadPane holds the one-in-flight guard.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type * as IdentityModule from "@/lib/identity";
import type * as ChatStoreModule from "@/store/chatStore";

const fetchMock = vi.hoisted(() => vi.fn());
const sessionResult = vi.hoisted(() => ({
  current: { session: { permissionLevel: 2 }, isLoading: false, error: null } as {
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

import { useAgentThreadStore } from "./agent/agentThreadStore";
import type { ChatMessage, DirectoryEntry } from "./chatApi";
import { MessageItem, type MessageView } from "./MessageItem";
import { CHAT_TEST_IDS } from "./testids";
import { ThreadPane } from "./ThreadPane";

const directory = new Map<string, DirectoryEntry>([
  ["anna", { id: "anna", kind: "user", display_name: "Anna" }],
  ["bob", { id: "bob", kind: "user", display_name: "Bob" }],
]);

function view(overrides: Partial<MessageView> = {}): MessageView {
  return {
    key: "m-reply",
    id: "m-reply",
    clientId: null,
    channelId: "c-billing",
    threadRootId: "m-root",
    authorId: "bob",
    authorKind: "user",
    createdAtMs: Date.now(),
    body: "Bitte auch die Migration prüfen",
    status: "sent",
    ...overrides,
  };
}

function renderItem(item: MessageView, onForward?: (id: string) => void) {
  return render(
    <MemoryRouter>
      <MessageItem message={item} directory={directory} onForward={onForward} />
    </MemoryRouter>,
  );
}

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

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function page(items: ChatMessage[]) {
  return { items, has_older: false, has_more: false, next_after: null, next_before: null };
}

function Providers({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TooltipProvider>{children}</TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

afterEach(cleanup);

describe("MessageItem forward status", () => {
  it("AC-041a a reply that was not forwarded shows nicht an Agent", () => {
    renderItem(view({ forwardState: "not_forwarded" }));
    expect(screen.getByTestId(CHAT_TEST_IDS.forwardNotForwarded)).toHaveTextContent(
      "nicht an Agent",
    );
    expect(screen.queryByTestId(CHAT_TEST_IDS.forwardFailed)).toBeNull();
  });

  it("AC-041a a forwarded reply shows no delivery label", () => {
    renderItem(view({ forwardState: "forwarded" }));
    expect(screen.queryByTestId(CHAT_TEST_IDS.forwardNotForwarded)).toBeNull();
    expect(screen.queryByTestId(CHAT_TEST_IDS.forwardFailed)).toBeNull();
  });

  it("AC-054a a failed forward shows nicht zugestellt – erneut senden and one click retries once", () => {
    const onForward = vi.fn();
    renderItem(view({ forwardState: "failed" }), onForward);
    const failed = screen.getByTestId(CHAT_TEST_IDS.forwardFailed);
    expect(failed).toHaveTextContent("nicht zugestellt – erneut senden");
    // WCAG 2.5.3: the accessible name contains the visible label contiguously.
    const retry = within(failed).getByRole("button", { name: /erneut senden/ });
    expect(retry).toHaveAccessibleName(/^erneut senden/);
    expect(retry).toHaveAccessibleDescription(/nicht zugestellt/);
    expect(retry).toHaveAttribute("data-testid", CHAT_TEST_IDS.forwardRetry);
    fireEvent.click(retry);
    expect(onForward).toHaveBeenCalledTimes(1);
    expect(onForward).toHaveBeenCalledWith("m-reply");
  });
});

describe("ThreadPane forward retry", () => {
  const root = message({ id: "m-root", body: "<@omnigent_agent:agent> Tests laufen lassen" });
  const reply = message({
    id: "m-reply",
    thread_root_id: "m-root",
    author_id: "bob",
    body: "Bitte auch die Migration prüfen",
    forward_state: "failed",
  });
  let forwardCalls: number;
  let resolveForward: (state: string) => void;

  beforeEach(() => {
    forwardCalls = 0;
    fetchMock.mockReset();
    fetchMock.mockImplementation(async (url: string, init?: { method?: string }) => {
      const parsed = new URL(url, "http://test");
      if (parsed.pathname === "/v1/chat/messages/m-reply/forward" && init?.method === "POST") {
        forwardCalls += 1;
        return new Promise((resolve) => {
          resolveForward = (state) => resolve(jsonResponse({ forward_state: state }));
        });
      }
      if (parsed.pathname === "/v1/chat/channels/c-billing/messages") {
        const thread = parsed.searchParams.get("thread_root_id");
        return jsonResponse(page(thread === "m-root" ? [reply] : [root]));
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

  it("AC-054a a second click while the first retry runs sends no second forward", async () => {
    render(
      <Providers>
        <ThreadPane
          channelId="c-billing"
          threadRootId="m-root"
          channelLabel="#billing"
          me="bob"
          directory={directory}
        />
      </Providers>,
    );
    const retry = await screen.findByRole("button", { name: /erneut senden/ });
    fireEvent.click(retry);
    fireEvent.click(retry);
    await waitFor(() =>
      expect(screen.getByTestId(CHAT_TEST_IDS.forwardForwarding)).toHaveTextContent(
        "wird zugestellt…",
      ),
    );
    expect(screen.queryByTestId(CHAT_TEST_IDS.forwardFailed)).toBeNull();
    expect(screen.queryByTestId(CHAT_TEST_IDS.forwardRetry)).toBeNull();
    expect(forwardCalls).toBe(1);

    resolveForward("forwarded");
    await waitFor(() => expect(screen.queryByTestId(CHAT_TEST_IDS.forwardForwarding)).toBeNull());
    expect(screen.queryByTestId(CHAT_TEST_IDS.forwardFailed)).toBeNull();
    expect(forwardCalls).toBe(1);
  });
});
