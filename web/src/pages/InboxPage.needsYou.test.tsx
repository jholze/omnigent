import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useScopeCache", () => import("@/test/mockScopeCache"));

import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import type { NeedsYouResponse } from "@/chat/NeedsYouView";
import * as commentInboxHook from "@/hooks/useCommentInbox";
import * as conversationsHook from "@/hooks/useConversations";
import type * as identity from "@/lib/identity";
import { SidebarDataProvider } from "@/hooks/useSidebarData";

import { InboxPage } from "./InboxPage";

// The inbox with team chat on: the "Braucht dich" rows count in the header
// summary whatever the browser notification permission is (FR-012).

const fetchMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/identity", async (importActual) => ({
  ...(await importActual<typeof identity>()),
  authenticatedFetch: (...args: unknown[]) => fetchMock(...args),
}));
vi.mock("@/lib/CapabilitiesContext", () => ({
  useServerInfo: () => ({ features: { team_chat: true } }),
}));
vi.mock("@/hooks/useConversations", async (importActual) => ({
  ...(await importActual<typeof conversationsHook>()),
  useConversations: vi.fn(),
}));
vi.mock("@/hooks/useCommentInbox", () => ({ useCommentInbox: vi.fn() }));
vi.mock("@/lib/sessionsApi", () => ({
  getSession: vi.fn(),
  approve: vi.fn(),
  fetchSessionItemsPage: vi.fn(),
}));

const ONE_MENTION: NeedsYouResponse = {
  mentions: [
    {
      channel_id: "c-billing",
      channel_kind: "channel",
      channel_name: "billing",
      thread_root_id: null,
      message_id: "m-mention",
      author_id: "jens@bloopark.de",
      author_label: "Jens",
      created_at: 1_700_000_000_000_000,
    },
  ],
  approvals: [],
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <SidebarDataProvider>
        <MemoryRouter initialEntries={["/inbox"]}>
          <InboxPage />
        </MemoryRouter>
      </SidebarDataProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  const requestPermission = vi.fn().mockResolvedValue("denied");
  vi.stubGlobal(
    "Notification",
    Object.assign(vi.fn(), { permission: "denied", requestPermission }),
  );
  fetchMock.mockImplementation(async (url: string) =>
    url === "/v1/chat/needs-you"
      ? { ok: true, status: 200, json: async () => ONE_MENTION }
      : { ok: false, status: 404, json: async () => ({}) },
  );
  vi.mocked(conversationsHook.useConversations).mockReturnValue({
    data: { pages: [{ data: [] }] },
    isLoading: false,
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: vi.fn(),
  } as unknown as ReturnType<typeof conversationsHook.useConversations>);
  vi.mocked(commentInboxHook.useCommentInbox).mockReturnValue({
    items: [],
    isLoading: false,
    failedCount: 0,
    retryFailed: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("InboxPage with Braucht dich", () => {
  it("AC-012a: with permission denied the inbox summary still counts the mention as 'need you'", async () => {
    renderPage();

    expect(await screen.findByText("1 need you")).toBeInTheDocument();
  });
});
