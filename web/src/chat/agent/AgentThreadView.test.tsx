// Open Agent-Thread: live output, status chip and approval cards.
// The session snapshot (`useSession`) and the stream bind are mocked; the
// test writes the registry entry the way the stream pump would.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { AnyBlock, BlockContext, ElicitationBlock } from "@/lib/blocks";
import type * as IdentityModule from "@/lib/identity";
import type * as ChatStoreModule from "@/store/chatStore";
import type * as SessionsApiModule from "@/lib/sessionsApi";
import type * as SonnerModule from "sonner";

const fetchMock = vi.hoisted(() => vi.fn());
const sessionResult = vi.hoisted(() => ({
  current: { session: null, isLoading: true, error: null } as {
    session: { permissionLevel: number | null } | null;
    isLoading: boolean;
    error: Error | null;
  },
}));
const ensureMock = vi.hoisted(() => vi.fn(async (_id: string) => {}));
const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), message: vi.fn() }));
const connectListeners = vi.hoisted(() => new Set<(id: string) => void>());
const getSessionSlimMock = vi.hoisted(() =>
  vi.fn(async (_id: string, _options?: unknown): Promise<{ status: string }> => ({
    status: "idle",
  })),
);

vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof IdentityModule>()),
  authenticatedFetch: (...args: unknown[]) => fetchMock(...args),
}));
vi.mock("@/hooks/useSession", () => ({ useSession: () => sessionResult.current }));
vi.mock("@/store/chatStore", async (importOriginal) => ({
  ...(await importOriginal<typeof ChatStoreModule>()),
  ensureConversationStreamed: (id: string) => ensureMock(id),
  releaseConversation: vi.fn(),
  subscribeConversationStreamConnected: (listener: (id: string) => void) => {
    connectListeners.add(listener);
    return () => connectListeners.delete(listener);
  },
}));
vi.mock("@/lib/sessionsApi", async (importOriginal) => ({
  ...(await importOriginal<typeof SessionsApiModule>()),
  getSessionSlim: (id: string, options?: unknown) => getSessionSlimMock(id, options),
}));
vi.mock("sonner", async (importOriginal) => ({
  ...(await importOriginal<typeof SonnerModule>()),
  toast: Object.assign(vi.fn(), toastMock),
}));

import { ApiError } from "@/lib/sessionsApi";
import { conversationRegistry } from "@/store/conversationRegistry";
import type { DirectoryEntry } from "../chatApi";
import { MessageItem, type MessageView } from "../MessageItem";
import { CHAT_TEST_IDS } from "../testids";
import { AgentThreadStatus } from "./AgentStatusChip";
import { SEND_FAILED_TEXT, useThreadApprovals } from "./agentApprovals";
import { useAgentThreadStore } from "./agentThreadStore";
import { AgentThreadView, OpenAgentSessionLink } from "./AgentThreadView";

const SESSION = "conv_agent";
const directory = new Map<string, DirectoryEntry>([
  ["anna", { id: "anna", kind: "user", display_name: "Anna" }],
  ["bob", { id: "bob", kind: "user", display_name: "Bob" }],
]);

function ctx(responseId = "resp_1", itemId: string | null = null): BlockContext {
  return { agent: "coder", depth: 0, turn: 0, timestamp: 0, responseId, itemId };
}

const userInput: AnyBlock = {
  type: "user_message",
  ctx: ctx("resp_user", "item_user"),
  content: [{ type: "input_text", text: "Thread-Kontext" }],
};

function chunk(text: string): AnyBlock {
  return { type: "text_chunk", ctx: ctx(), text };
}

function card(overrides: Partial<ElicitationBlock> = {}): ElicitationBlock {
  return {
    type: "elicitation",
    ctx: ctx(),
    elicitationId: "elic_1",
    message: "Migration anwenden?",
    phase: "tool_call",
    policyName: "",
    contentPreview: "",
    requestedSchema: {},
    status: "pending",
    response: null,
    ...overrides,
  };
}

function seedEntry(blocks: AnyBlock[], sessionStatus: "idle" | "running" | "waiting" | "failed") {
  const entry = conversationRegistry.acquire(SESSION);
  act(() => entry.setState({ blocks, sessionStatus, loadingConversation: false }));
  return entry;
}

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function withAccess(level: number | null) {
  sessionResult.current = { session: { permissionLevel: level }, isLoading: false, error: null };
}

function Providers({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <TooltipProvider>{children}</TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function renderView(overrides: Partial<Parameters<typeof AgentThreadView>[0]> = {}) {
  const props = {
    sessionId: SESSION,
    me: "anna",
    directory,
    ownerLabel: "Anna",
    agentReplyCount: 0,
    sharingNoticeStored: false,
    ...overrides,
  };
  const view = render(
    <Providers>
      <AgentThreadView {...props} />
    </Providers>,
  );
  return {
    ...view,
    rerenderWith: (next: Partial<typeof props>) =>
      view.rerender(
        <Providers>
          <AgentThreadView {...props} {...next} />
        </Providers>,
      ),
  };
}

function blockOf(id = "elic_1"): ElicitationBlock | undefined {
  return conversationRegistry
    .peek(SESSION)
    ?.getState()
    .blocks.find(
      (block): block is ElicitationBlock =>
        block.type === "elicitation" && block.elicitationId === id,
    );
}

function resolveElsewhere(action: "accept" | "decline", decidedBy?: string) {
  const entry = conversationRegistry.peek(SESSION)!;
  act(() =>
    entry.setState((state) => ({
      blocks: state.blocks.map((block) =>
        block.type === "elicitation"
          ? {
              ...block,
              status: "responded",
              response: { action, ...(decidedBy ? { decidedBy } : {}) },
            }
          : block,
      ),
    })),
  );
}

function emitConnected(id = SESSION) {
  act(() => {
    for (const listener of connectListeners) listener(id);
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  ensureMock.mockClear();
  toastMock.error.mockClear();
  getSessionSlimMock.mockReset();
  getSessionSlimMock.mockImplementation(async () => ({ status: "idle" }));
  useThreadApprovals.setState({ sending: {}, resolvers: {}, forbidden: {}, failed: {} });
  useAgentThreadStore.setState({ threads: {}, sessions: {}, watchers: {} });
});

afterEach(() => {
  cleanup();
  conversationRegistry.clear();
});

describe("AgentThreadView live output", () => {
  it("AC-045a a READ viewer sees the streamed text grow without a reload", async () => {
    withAccess(1);
    const entry = seedEntry([userInput], "running");
    renderView({ me: "jens" });
    expect(ensureMock).toHaveBeenCalledWith(SESSION);
    const parts = ["Tests ", "sind ", "gr", "ü", "n"];
    const blocks: AnyBlock[] = [userInput];
    // One chunk per step; each must paint before the next arrives.
    const streamFrom = async (index: number): Promise<void> => {
      if (index >= parts.length) return;
      blocks.push(chunk(parts[index]));
      act(() => entry.setState({ blocks: [...blocks] }));
      // Live text paints on the next animation frame (FR-080).
      await act(
        () =>
          new Promise<void>((resolve) => {
            requestAnimationFrame(() => {
              resolve();
            });
          }),
      );
      const live = screen.getByTestId("agent-thread-live");
      expect(live.textContent).toContain(
        parts
          .slice(0, index + 1)
          .join("")
          .trim(),
      );
      await streamFrom(index + 1);
    };
    await streamFrom(0);
    expect(screen.getByTestId("agent-thread-live").textContent).toContain("Tests sind grün");
  });

  it("AC-045b without a grant no live view opens; the hint names the starter", () => {
    sessionResult.current = {
      session: null,
      isLoading: false,
      error: new ApiError("forbidden", 403, null),
    };
    renderView({ me: "bob" });
    expect(screen.getByText("Live-Ansicht nur für Anna")).toBeInTheDocument();
    expect(ensureMock).not.toHaveBeenCalled();
    expect(conversationRegistry.has(SESSION)).toBe(false);
  });

  it("AC-045b a stored server notice is not repeated", () => {
    sessionResult.current = {
      session: null,
      isLoading: false,
      error: new ApiError("not found", 404, null),
    };
    renderView({ me: "bob", sharingNoticeStored: true });
    expect(screen.queryByText(/Live-Ansicht nur für/)).toBeNull();
    expect(ensureMock).not.toHaveBeenCalled();
  });

  it("AC-044b a failed turn shows Fehler and one system line, never the raw error", () => {
    withAccess(4);
    useAgentThreadStore.setState({
      threads: {
        "m-root": {
          channelId: "c1",
          rootId: "m-root",
          agentState: "active",
          sessionId: SESSION,
        },
      },
    });
    seedEntry(
      [
        userInput,
        chunk("Ich prüfe"),
        {
          type: "error",
          ctx: ctx(),
          source: "llm",
          code: "llm_error",
          message: "Traceback: secret-token-123",
        },
      ],
      "failed",
    );
    const systemLine: MessageView = {
      key: "m-sys",
      id: "m-sys",
      clientId: null,
      channelId: "c1",
      threadRootId: "m-root",
      authorId: "system",
      authorKind: "system",
      createdAtMs: Date.now(),
      body: "Agent-Fehler: internal",
      status: "sent",
    };
    render(
      <Providers>
        <AgentThreadStatus rootId="m-root" />
        <ul>
          <MessageItem message={systemLine} directory={directory} />
        </ul>
        <AgentThreadView
          sessionId={SESSION}
          me="anna"
          directory={directory}
          ownerLabel="Anna"
          agentReplyCount={1}
          sharingNoticeStored={false}
        />
      </Providers>,
    );
    expect(screen.getByTestId(CHAT_TEST_IDS.agentStatusChip).textContent).toBe("Fehler");
    expect(screen.getAllByText("Agent-Fehler: internal")).toHaveLength(1);
    expect(document.body.textContent).not.toContain("secret-token-123");
  });

  it("the mirrored reply replaces the live turn once it is stored", () => {
    withAccess(1);
    const entry = seedEntry([userInput, chunk("Fertig.")], "running");
    const view = renderView({ agentReplyCount: 0 });
    expect(screen.getByTestId("agent-thread-live")).toBeInTheDocument();
    act(() => entry.setState({ sessionStatus: "idle" }));
    // Still shown until the stored reply arrives, so the answer does not blink.
    expect(screen.getByTestId("agent-thread-live").textContent).toContain("Fertig.");
    view.rerenderWith({ agentReplyCount: 1 });
    expect(screen.queryByTestId("agent-thread-live")).toBeNull();
  });
});

describe("AgentThreadStatus (open thread)", () => {
  function seedThread() {
    useAgentThreadStore.setState({
      threads: {
        "m-root": { channelId: "c1", rootId: "m-root", agentState: "active", sessionId: SESSION },
      },
    });
  }

  function chipText(): string | null {
    return within(screen.getByRole("status")).getByTestId(CHAT_TEST_IDS.agentStatusChip)
      .textContent;
  }

  it("AC-044a the chip shows braucht Antwort as soon as the stream reports waiting with an open approval", () => {
    withAccess(4);
    seedThread();
    const entry = seedEntry([userInput], "running");
    render(
      <Providers>
        <AgentThreadStatus rootId="m-root" />
      </Providers>,
    );
    expect(chipText()).toBe("arbeitet");
    const started = performance.now();
    act(() => entry.setState({ sessionStatus: "waiting", blocks: [userInput, card()] }));
    expect(chipText()).toBe("braucht Antwort");
    expect(performance.now() - started).toBeLessThan(1000);
  });

  it("AC-044c waiting with no open approval shows arbeitet im Hintergrund", () => {
    withAccess(4);
    seedThread();
    const entry = seedEntry([userInput], "running");
    render(
      <Providers>
        <AgentThreadStatus rootId="m-root" />
      </Providers>,
    );
    act(() => entry.setState({ sessionStatus: "waiting" }));
    expect(chipText()).toBe("arbeitet im Hintergrund");
    const chip = within(screen.getByRole("status")).getByTestId(CHAT_TEST_IDS.agentStatusChip);
    expect(chip).toHaveAttribute("data-state", "background");
    expect(chip.className).not.toContain("warning");
  });

  it("AC-044a a status published between snapshot and stream connect shows once connected", async () => {
    withAccess(4);
    seedThread();
    // Snapshot said running; the server went idle before the stream connected.
    seedEntry([userInput], "running");
    getSessionSlimMock.mockImplementation(async () => ({ status: "idle" }));
    render(
      <Providers>
        <AgentThreadStatus rootId="m-root" />
        <AgentThreadView
          sessionId={SESSION}
          me="anna"
          directory={directory}
          ownerLabel="Anna"
          agentReplyCount={0}
          sharingNoticeStored={false}
        />
      </Providers>,
    );
    expect(chipText()).toBe("arbeitet");
    expect(getSessionSlimMock).not.toHaveBeenCalled();
    const started = performance.now();
    emitConnected();
    await waitFor(() => expect(chipText()).toBe("fertig"), { timeout: 1000 });
    expect(performance.now() - started).toBeLessThan(1000);
    expect(getSessionSlimMock).toHaveBeenCalledWith(SESSION, expect.anything());
  });

  it("a live status event during the re-read wins over the older snapshot", async () => {
    withAccess(4);
    seedThread();
    const entry = seedEntry([userInput], "running");
    let answer: (value: { status: string }) => void = () => {};
    getSessionSlimMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    );
    render(
      <Providers>
        <AgentThreadStatus rootId="m-root" />
        <AgentThreadView
          sessionId={SESSION}
          me="anna"
          directory={directory}
          ownerLabel="Anna"
          agentReplyCount={0}
          sharingNoticeStored={false}
        />
      </Providers>,
    );
    emitConnected();
    await waitFor(() => expect(getSessionSlimMock).toHaveBeenCalled());
    act(() => entry.setState({ sessionStatus: "waiting", blocks: [userInput, card()] }));
    await act(async () => answer({ status: "running" }));
    expect(chipText()).toBe("braucht Antwort");
  });
});

describe("Approval cards in the thread", () => {
  it("AC-055a a requested approval shows Genehmigen and Ablehnen inline", () => {
    withAccess(2);
    seedEntry([userInput, chunk("Ich brauche eine Freigabe."), card()], "running");
    renderView();
    const live = screen.getByTestId("agent-thread-live");
    const approval = within(live).getByTestId(CHAT_TEST_IDS.approvalCard);
    expect(approval).toHaveAttribute("data-state", "pending");
    expect(within(approval).getByText("Migration anwenden?")).toBeInTheDocument();
    expect(within(approval).getByRole("button", { name: "Genehmigen" })).toBeInTheDocument();
    expect(within(approval).getByRole("button", { name: "Ablehnen" })).toBeInTheDocument();
    // The card sits after the text that led to it.
    expect(live.textContent?.indexOf("Freigabe.")).toBeLessThan(
      live.textContent?.indexOf("Migration anwenden?") ?? -1,
    );
  });

  it("AC-055b a card opened while the thread was closed is there on open", () => {
    withAccess(2);
    // History hydration already holds the open request when the view mounts.
    seedEntry([userInput, card()], "idle");
    renderView();
    const approval = screen.getByTestId(CHAT_TEST_IDS.approvalCard);
    expect(approval).toHaveAttribute("data-state", "pending");
    expect(within(approval).getByRole("button", { name: "Genehmigen" })).toBeInTheDocument();
  });

  it("AC-056a Genehmigen resolves with 202 and the card shows Genehmigt von Anna", async () => {
    withAccess(2);
    seedEntry([userInput, card()], "running");
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") return jsonResponse({ queued: false }, 202);
      return jsonResponse({ status: "pending" });
    });
    renderView();
    fireEvent.click(screen.getByRole("button", { name: "Genehmigen" }));
    expect(screen.getByText("Antwort wird gesendet…")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId(CHAT_TEST_IDS.approvalCard)).toHaveAttribute(
        "data-state",
        "responded",
      ),
    );
    expect(screen.getByText("Genehmigt von Anna")).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe(`/v1/sessions/${SESSION}/elicitations/elic_1/resolve`);
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ action: "accept" });
    expect(toastMock.error).not.toHaveBeenCalled();
  });

  it("AC-056b a READ viewer gets no buttons and the read-only note", () => {
    withAccess(1);
    seedEntry([userInput, card()], "running");
    renderView({ me: "bob" });
    const approval = screen.getByTestId(CHAT_TEST_IDS.approvalCard);
    expect(within(approval).queryByRole("button")).toBeNull();
    expect(
      within(approval).getByText("Nur Aufrufer und Bearbeiter können antworten"),
    ).toBeInTheDocument();
  });

  it("AC-057a a card resolved elsewhere switches to the server verdict", () => {
    withAccess(2);
    seedEntry([userInput, card()], "running");
    renderView();
    resolveElsewhere("accept");
    const approval = screen.getByTestId(CHAT_TEST_IDS.approvalCard);
    expect(approval).toHaveAttribute("data-state", "responded");
    // An event without decided_by carries the verdict only.
    expect(within(approval).getByText("Genehmigt")).toBeInTheDocument();
    expect(within(approval).queryByRole("button", { name: "Genehmigen" })).toBeNull();
  });

  it("AC-057a with decided_by the card names the decider from the directory", () => {
    withAccess(2);
    seedEntry([userInput, card()], "running");
    renderView();
    resolveElsewhere("decline", "bob");
    const approval = screen.getByTestId(CHAT_TEST_IDS.approvalCard);
    expect(within(approval).getByText("Abgelehnt von Bob")).toBeInTheDocument();
    expect(within(approval).queryByText(/^Abgelehnt$/)).toBeNull();
  });

  it("AC-057a an unknown decider shows the user id", () => {
    withAccess(2);
    seedEntry([userInput, card()], "running");
    renderView();
    resolveElsewhere("accept", "u_carla");
    expect(screen.getByText("Genehmigt von u_carla")).toBeInTheDocument();
  });

  it("AC-068a a bot decider shows the directory name with its owner", () => {
    withAccess(2);
    seedEntry([userInput, card()], "running");
    const bot = "agent:acct_grok";
    renderView({
      directory: new Map([
        ...directory,
        [bot, { id: bot, kind: "agent_account", display_name: "Grok (Jens)" }],
      ]),
    });
    resolveElsewhere("accept", bot);
    const approval = screen.getByTestId(CHAT_TEST_IDS.approvalCard);
    expect(within(approval).getByText("Genehmigt von Grok (Jens)")).toBeInTheDocument();
  });

  it("a stale click uses the verdict and decider the GET reports, no toast", async () => {
    withAccess(2);
    seedEntry([userInput, card()], "running");
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") return jsonResponse({ queued: false }, 202);
      return jsonResponse({ status: "resolved", verdict: "accept", decided_by: "bob" });
    });
    renderView();
    fireEvent.click(screen.getByRole("button", { name: "Ablehnen" }));
    await waitFor(() => expect(blockOf()?.status).toBe("responded"));
    expect(blockOf()?.response).toEqual({ action: "accept", decidedBy: "bob" });
    expect(screen.getByText("Genehmigt von Bob")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    expect(toastMock.error).not.toHaveBeenCalled();
  });

  it("a bare resolved GET stays the neutral Bereits beantwortet", async () => {
    withAccess(2);
    seedEntry([userInput, card()], "running");
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") return jsonResponse({ queued: false }, 202);
      return jsonResponse({ status: "resolved" });
    });
    renderView();
    fireEvent.click(screen.getByRole("button", { name: "Genehmigen" }));
    await waitFor(() => expect(blockOf()?.status).toBe("responded"));
    expect(blockOf()?.response).toEqual({ action: "auto_resolved" });
    expect(screen.getByText("Bereits beantwortet")).toBeInTheDocument();
    expect(toastMock.error).not.toHaveBeenCalled();
  });

  it("AC-058a a stale card shows the stored verdict on Ablehnen, without an error toast", async () => {
    withAccess(2);
    seedEntry([userInput, card()], "running");
    let answerCheck: (value: unknown) => void = () => {};
    fetchMock.mockImplementation((_url: string, init?: RequestInit) => {
      if (init?.method === "POST") return Promise.resolve(jsonResponse({ queued: false }, 202));
      return new Promise((resolve) => {
        answerCheck = resolve;
      });
    });
    renderView();
    fireEvent.click(screen.getByRole("button", { name: "Ablehnen" }));
    // Jens's verdict reaches this tab while the check is in flight.
    resolveElsewhere("accept");
    await act(async () => answerCheck(jsonResponse({ status: "resolved" })));
    await waitFor(() => expect(useThreadApprovals.getState().sending).toEqual({}));
    const approval = screen.getByTestId(CHAT_TEST_IDS.approvalCard);
    expect(within(approval).getByText("Genehmigt")).toBeInTheDocument();
    expect(within(approval).queryByText(/Abgelehnt/)).toBeNull();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    expect(toastMock.error).not.toHaveBeenCalled();
    expect(screen.queryByText(SEND_FAILED_TEXT)).toBeNull();
  });

  it("409 from resolve shows the server state, no error line, no toast", async () => {
    withAccess(2);
    seedEntry([userInput, card()], "running");
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") return jsonResponse({ detail: "already resolved" }, 409);
      return jsonResponse({ status: "pending" });
    });
    renderView();
    fireEvent.click(screen.getByRole("button", { name: "Ablehnen" }));
    await waitFor(() => expect(blockOf()?.status).toBe("responded"));
    expect(blockOf()?.response).toEqual({ action: "auto_resolved" });
    expect(screen.getByText("Bereits beantwortet")).toBeInTheDocument();
    expect(screen.queryByText(SEND_FAILED_TEXT)).toBeNull();
    expect(toastMock.error).not.toHaveBeenCalled();
  });

  it("403 from resolve turns the card read-only, no toast", async () => {
    withAccess(2);
    seedEntry([userInput, card()], "running");
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") return jsonResponse({ detail: "forbidden" }, 403);
      return jsonResponse({ status: "pending" });
    });
    renderView();
    fireEvent.click(screen.getByRole("button", { name: "Genehmigen" }));
    await waitFor(() =>
      expect(screen.getByText("Nur Aufrufer und Bearbeiter können antworten")).toBeInTheDocument(),
    );
    expect(blockOf()?.status).toBe("pending");
    expect(toastMock.error).not.toHaveBeenCalled();
  });

  it("a network failure keeps the card open and says so on the card", async () => {
    withAccess(2);
    seedEntry([userInput, card()], "running");
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") throw new TypeError("Failed to fetch");
      return jsonResponse({ status: "pending" });
    });
    renderView();
    fireEvent.click(screen.getByRole("button", { name: "Genehmigen" }));
    expect(
      await screen.findByText("Antwort nicht gesendet. Bitte erneut versuchen."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Genehmigen" })).toBeInTheDocument();
    expect(toastMock.error).not.toHaveBeenCalled();
  });
});

describe("OpenAgentSessionLink (thread header)", () => {
  function renderLink() {
    return render(
      <Providers>
        <OpenAgentSessionLink sessionId={SESSION} />
      </Providers>,
    );
  }

  it("AC-085a without a grant the Session öffnen link does not render", () => {
    sessionResult.current = {
      session: null,
      isLoading: false,
      error: new ApiError("not found", 404, null),
    };
    renderLink();
    expect(screen.queryByTestId(CHAT_TEST_IDS.threadOpenSession)).toBeNull();
  });

  it("AC-085a while the session snapshot loads the link does not render", () => {
    sessionResult.current = { session: null, isLoading: true, error: null };
    renderLink();
    expect(screen.queryByTestId(CHAT_TEST_IDS.threadOpenSession)).toBeNull();
  });

  it.each([
    ["read", 1],
    ["edit", 2],
  ] as const)("AC-085a a %s grant shows Session öffnen linking to the session", (_name, level) => {
    withAccess(level);
    renderLink();
    const link = screen.getByTestId(CHAT_TEST_IDS.threadOpenSession);
    expect(link).toHaveTextContent("Session öffnen");
    expect(link).toHaveAttribute("href", `/c/${SESSION}`);
  });
});
