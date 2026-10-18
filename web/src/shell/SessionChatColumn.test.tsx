import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { emitHumanChat } from "@/lib/humanChat";
import { authenticatedFetch } from "@/lib/identity";
import {
  SessionChatColumn,
  SessionChatDock,
  SessionChatPane,
  resetSessionChatStore,
  setSessionChatPlacement,
  useSessionChatPlacement,
  useSessionChatRowUnreadTip,
} from "./SessionChatColumn";

vi.mock("@/lib/identity", () => ({
  authenticatedFetch: vi.fn(),
  getCurrentUserId: () => null,
}));

const fetchMock = vi.mocked(authenticatedFetch);
const SESSION = "sess-1";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function line(id: string, body: string) {
  return { id, created_by: "jens@example.com", created_at: 1_700_000_000_000_000, body };
}

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  resetSessionChatStore();
  setSessionChatPlacement("panel");
  try {
    localStorage.removeItem("omnigent:session-chat-placement");
    localStorage.removeItem("omnigent:session-chat-width");
  } catch {
    // This Node build exposes a localStorage stub with no Storage methods.
  }
});

async function expectHidden(response: Response) {
  const gate = deferred<Response>();
  fetchMock.mockReturnValueOnce(gate.promise);
  render(
    <>
      <span>Anna is here</span>
      <SessionChatColumn conversationId={SESSION} />
    </>,
  );
  await act(async () => {
    gate.resolve(response);
    await gate.promise;
  });
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: "Session chat" })).toBeNull();
  expect(screen.getByText("Anna is here")).toBeInTheDocument();
}

describe("SessionChatColumn", () => {
  it("stays hidden when GET returns 403", async () => {
    await expectHidden(jsonResponse({ error: { code: "forbidden" } }, 403));
  });

  it("stays hidden when GET returns 404", async () => {
    await expectHidden(jsonResponse({ error: { code: "not_found" } }, 404));
  });

  it("stays hidden when the session is unshared and has no lines", async () => {
    await expectHidden(jsonResponse({ items: [], shared: false }));
  });

  it("shows the control when the session is shared or already has a line", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [], shared: true }));
    const shared = render(<SessionChatColumn conversationId={SESSION} />);
    expect(await screen.findByRole("button", { name: "Session chat" })).toBeInTheDocument();
    shared.unmount();
    fetchMock.mockReset();

    fetchMock.mockResolvedValueOnce(
      jsonResponse({ items: [line("m1", "I'll look at the test")], shared: false }),
    );
    render(<SessionChatColumn conversationId={SESSION} />);
    expect(await screen.findByRole("button", { name: "Session chat" })).toBeInTheDocument();
  });

  it("asks the shell to open the panel instead of floating a column", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [], shared: true }));
    const onOpen = vi.fn();
    render(<SessionChatColumn conversationId={SESSION} onOpen={onOpen} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Session chat" }));

    expect(onOpen).toHaveBeenCalledOnce();
    expect(screen.queryByTestId("session-human-chat-column")).toBeNull();
    expect(screen.queryByPlaceholderText("Message the people in this session")).toBeNull();
  });

  it("renders the transcript in the pane, not as side chat or comments", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [], shared: true }));
    render(
      <>
        <SessionChatColumn conversationId={SESSION} />
        <SessionChatPane onSplit={() => setSessionChatPlacement("split")} />
      </>,
    );

    const column = await screen.findByTestId("session-human-chat-column");
    expect(column).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Message the people in this session")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Split beside the agent" })).toBeInTheDocument();
    expect(screen.queryByText("Comments")).toBeNull();
    expect(screen.queryByTestId("comments-panel")).toBeNull();
    expect(screen.queryByTestId("side-chat-send")).toBeNull();
    expect(screen.queryByRole("tab", { name: /sidechat/i })).toBeNull();
  });

  it("posts the line to /chat and not to /events", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [], shared: true }));
    fetchMock.mockResolvedValueOnce(jsonResponse(line("m1", "I'll look at the test"), 201));
    render(
      <>
        <SessionChatColumn conversationId={SESSION} />
        <SessionChatPane />
      </>,
    );
    const user = userEvent.setup();
    await user.type(
      await screen.findByPlaceholderText("Message the people in this session"),
      "I'll look at the test{Enter}",
    );

    await waitFor(() => expect(screen.getByText("I'll look at the test")).toBeInTheDocument());
    const post = fetchMock.mock.calls.find(
      (call) => (call[1] as RequestInit | undefined)?.method === "POST",
    );
    expect(post).toBeDefined();
    if (!post) return;
    const init = post[1] as RequestInit;
    expect(post[0]).toBe(`/v1/sessions/${SESSION}/chat`);
    expect(JSON.parse(String(init.body))).toEqual({
      body: "I'll look at the test",
    });
    expect(fetchMock.mock.calls.every((call) => !String(call[0]).includes("/events"))).toBe(true);
  });

  it("marks a closed column unread and takes the tooltip from the next GET", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [line("m0", "earlier")], shared: true }));
    render(<SessionChatColumn conversationId={SESSION} />);
    const button = await screen.findByRole("button", { name: "Session chat" });
    expect(button).not.toHaveAttribute("title");
    expect(screen.queryByTestId("session-chat-unread")).toBeNull();

    const gate = deferred<Response>();
    fetchMock.mockReturnValueOnce(gate.promise);
    act(() => {
      emitHumanChat({
        conversationId: SESSION,
        id: "m1",
        createdAt: 1_700_000_000_000_001,
      });
    });
    expect(screen.getByTestId("session-chat-unread")).toBeInTheDocument();
    expect(button).not.toHaveAttribute("title");

    await act(async () => {
      gate.resolve(
        jsonResponse({
          items: [line("m0", "earlier"), line("m1", "I'll look at the test")],
          shared: true,
        }),
      );
      await gate.promise;
    });
    await waitFor(() => expect(button).toHaveAttribute("title", "I'll look at the test"));
  });

  it("docks the transcript between the agent and the panel, then moves it back", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ items: [line("m1", "Ada is here.")], shared: true }),
    );
    function Surface() {
      const placement = useSessionChatPlacement();
      return (
        <>
          <SessionChatColumn conversationId={SESSION} />
          {placement === "split" ? (
            <SessionChatDock onMoveToPanel={() => setSessionChatPlacement("panel")} />
          ) : (
            <SessionChatPane onSplit={() => setSessionChatPlacement("split")} />
          )}
        </>
      );
    }
    render(<Surface />);
    expect(await screen.findByText("Ada is here.")).toBeInTheDocument();
    expect(screen.queryByTestId("session-human-chat-dock")).toBeNull();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Split beside the agent" }));
    const dock = screen.getByTestId("session-human-chat-dock");
    expect(dock).toBeInTheDocument();
    expect(dock).toHaveStyle({ width: "320px" });
    expect(screen.getByText("Ada is here.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Split beside the agent" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Move into the panel" }));
    expect(screen.queryByTestId("session-human-chat-dock")).toBeNull();
    expect(screen.getByRole("button", { name: "Split beside the agent" })).toBeInTheDocument();
    expect(screen.getByText("Ada is here.")).toBeInTheDocument();
  });

  it("keeps a session-chat dot after the sidebar switches to another session", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ items: [line("m0", "earlier")], shared: true }));
    function Rows({ id }: { id: string }) {
      const first = useSessionChatRowUnreadTip(SESSION);
      const second = useSessionChatRowUnreadTip("sess-2");
      return (
        <>
          <SessionChatColumn conversationId={id} />
          <span>{first === null ? "first-clear" : "first-dotted"}</span>
          <span>{second === null ? "second-clear" : "second-dotted"}</span>
        </>
      );
    }
    const view = render(<Rows id={SESSION} />);
    await screen.findByRole("button", { name: "Session chat" });
    const gate = deferred<Response>();
    fetchMock.mockReturnValueOnce(gate.promise);
    act(() => {
      emitHumanChat({
        conversationId: SESSION,
        id: "m1",
        createdAt: 1_700_000_000_000_001,
      });
    });
    expect(screen.getByText("first-dotted")).toBeInTheDocument();

    view.rerender(<Rows id="sess-2" />);
    expect(screen.getByText("first-dotted")).toBeInTheDocument();
    expect(screen.getByText("second-clear")).toBeInTheDocument();

    await act(async () => {
      gate.resolve(
        jsonResponse({
          items: [line("m0", "earlier"), line("m1", "I'll look at the test")],
          shared: true,
        }),
      );
      await gate.promise;
    });
  });
});
