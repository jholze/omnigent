// Agent-Thread watch-set and channel-list chips (FR-047). The socket is
// mocked: the test sees which ids reach `setWatched` and pushes frames.
import { act, cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConversationsInfiniteData } from "@/lib/sessionListCache";
import type { SessionUpdatesFrame } from "@/lib/sessionUpdatesSocket";

const socket = vi.hoisted(() => ({
  setWatched: vi.fn(),
  listeners: new Set<(frame: unknown) => void>(),
}));
vi.mock("@/lib/sessionUpdatesSocket", () => ({
  sessionUpdatesSocket: {
    start: vi.fn(),
    stop: vi.fn(),
    setWatched: (ids: string[]) => socket.setWatched(ids),
    subscribe: (fn: (frame: unknown) => void) => {
      socket.listeners.add(fn);
      return () => socket.listeners.delete(fn);
    },
  },
}));

import { SessionUpdatesProvider } from "@/hooks/SessionUpdatesProvider";
import { CHAT_TEST_IDS } from "../testids";
import { acceptChatFrame, parseChatFrame } from "../useChatFrames";
import { AgentRootChip } from "./AgentStatusChip";
import { useAgentThreadStore, useWatchAgentSessions } from "./agentThreadStore";

function emit(frame: SessionUpdatesFrame | Record<string, unknown>) {
  act(() => {
    for (const listener of socket.listeners) listener(frame);
  });
}

function threadFrame(overrides: Record<string, unknown> = {}) {
  return {
    type: "chat",
    kind: "thread.updated",
    channel_id: "c-billing",
    root_message_id: "m-root",
    reply_count: 1,
    agent_state: "active",
    session_id: "conv_agent",
    bot_streak: 0,
    ...overrides,
  };
}

function lastWatched(): string[] {
  const call = socket.setWatched.mock.calls.at(-1);
  return [...((call?.[0] ?? []) as string[])].sort();
}

function ChannelRoot({ sessionIds }: { sessionIds: string[] }) {
  useWatchAgentSessions("channel:c-billing", sessionIds);
  return <AgentRootChip rootId="m-root" />;
}

function renderChannel(client: QueryClient, sessionIds: string[]) {
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SessionUpdatesProvider>
          <ChannelRoot sessionIds={sessionIds} />
        </SessionUpdatesProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function seedSidebar(client: QueryClient, ids: string[]) {
  const data: ConversationsInfiniteData = {
    pages: [
      {
        data: ids.map((id) => ({
          id,
          object: "conversation" as const,
          title: null,
          created_at: 0,
          updated_at: 0,
          labels: {},
          permission_level: 4,
        })),
        first_id: ids[0] ?? null,
        last_id: ids.at(-1) ?? null,
        has_more: false,
      },
    ],
    pageParams: [undefined],
  };
  client.setQueryData(["conversations", "", false], data);
}

beforeEach(() => {
  socket.setWatched.mockClear();
  useAgentThreadStore.setState({ threads: {}, sessions: {}, watchers: {} });
});

afterEach(() => {
  cleanup();
  socket.listeners.clear();
});

describe("Agent-Thread watch-set", () => {
  it("adds on-screen Agent-Thread sessions to the socket watch-set and drops them after", () => {
    const client = new QueryClient();
    seedSidebar(client, ["conv_mine"]);
    const view = renderChannel(client, ["conv_agent"]);
    expect(lastWatched()).toEqual(["conv_agent", "conv_mine"]);
    view.unmount();
    expect(useAgentThreadStore.getState().watchers).toEqual({});
  });

  it("AC-047a the root chip follows the watched row with no thread open", () => {
    const client = new QueryClient();
    seedSidebar(client, ["conv_mine"]);
    acceptChatFrame(parseChatFrame(threadFrame())!);
    renderChannel(client, ["conv_agent"]);

    emit({
      type: "changed",
      items: [{ id: "conv_agent", status: "running", pending_elicitations_count: 0, labels: {} }],
    });
    expect(screen.getByTestId(CHAT_TEST_IDS.agentStatusChip).textContent).toBe("arbeitet");

    emit({
      type: "changed",
      items: [{ id: "conv_agent", status: "running", pending_elicitations_count: 1, labels: {} }],
    });
    const chip = screen.getByTestId(CHAT_TEST_IDS.agentStatusChip);
    expect(chip.textContent).toBe("braucht Antwort");
    expect(chip).toHaveAttribute("data-source", "session");
  });

  it("rows only the chat watches stay out of the sidebar list", () => {
    const client = new QueryClient();
    seedSidebar(client, ["conv_mine"]);
    acceptChatFrame(parseChatFrame(threadFrame())!);
    renderChannel(client, ["conv_agent"]);
    emit({
      type: "snapshot",
      items: [
        { id: "conv_mine", status: "idle", labels: {} },
        { id: "conv_agent", status: "running", labels: {} },
      ],
    });
    const data = client.getQueryData<ConversationsInfiniteData>(["conversations", "", false]);
    expect(data?.pages[0].data.map((row) => row.id)).toEqual(["conv_mine"]);
  });

  it("AC-047b without a grant the root shows the last chat_threads state", () => {
    const client = new QueryClient();
    renderChannel(client, ["conv_agent"]);
    // Members get the thread frame; the socket never sends a row they may not read.
    emit(threadFrame({ agent_state: "starting", session_id: null }));
    let chip = screen.getByTestId(CHAT_TEST_IDS.agentStatusChip);
    expect(chip.textContent).toBe("startet…");
    expect(chip).toHaveAttribute("data-source", "thread");

    emit(threadFrame({ agent_state: "active" }));
    chip = screen.getByTestId(CHAT_TEST_IDS.agentStatusChip);
    expect(chip.textContent).toBe("aktiv");
    expect(chip).toHaveAttribute("data-source", "thread");
  });

  it("a removed row falls back to the thread state", () => {
    const client = new QueryClient();
    acceptChatFrame(parseChatFrame(threadFrame())!);
    renderChannel(client, ["conv_agent"]);
    emit({ type: "changed", items: [{ id: "conv_agent", status: "idle", labels: {} }] });
    expect(screen.getByTestId(CHAT_TEST_IDS.agentStatusChip).textContent).toBe("fertig");
    emit({ type: "removed", ids: ["conv_agent"] });
    expect(screen.getByTestId(CHAT_TEST_IDS.agentStatusChip).textContent).toBe("aktiv");
  });

  it("the session label links a root to its session before any thread frame", () => {
    const client = new QueryClient();
    renderChannel(client, []);
    emit({
      type: "snapshot",
      items: [
        {
          id: "conv_agent",
          status: "running",
          labels: {
            "omnigent.chat.v1": JSON.stringify({
              channel_id: "c-billing",
              thread_root_id: "m-root",
            }),
          },
        },
      ],
    });
    expect(useAgentThreadStore.getState().threads["m-root"]).toEqual({
      channelId: "c-billing",
      rootId: "m-root",
      agentState: null,
      sessionId: "conv_agent",
    });
    expect(screen.getByTestId(CHAT_TEST_IDS.agentStatusChip).textContent).toBe("arbeitet");
  });
});
