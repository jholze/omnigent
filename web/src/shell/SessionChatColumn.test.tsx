import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { emitHumanChat } from "@/lib/humanChat";
import { authenticatedFetch } from "@/lib/identity";
import { SessionChatColumn } from "./SessionChatColumn";

vi.mock("@/lib/identity", () => ({
  authenticatedFetch: vi.fn(),
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

  it("opens a column that is not side chat and not the comments panel", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [], shared: true }));
    render(<SessionChatColumn conversationId={SESSION} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Session chat" }));

    const column = screen.getByTestId("session-human-chat-column");
    expect(column).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Message the people in this session")).toBeInTheDocument();
    expect(screen.queryByText("Comments")).toBeNull();
    expect(screen.queryByTestId("comments-panel")).toBeNull();
    expect(screen.queryByTestId("side-chat-send")).toBeNull();
    expect(screen.queryByRole("tab", { name: /sidechat/i })).toBeNull();
  });

  it("posts the line to /chat and not to /events", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [], shared: true }));
    fetchMock.mockResolvedValueOnce(jsonResponse(line("m1", "I'll look at the test"), 201));
    render(<SessionChatColumn conversationId={SESSION} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Session chat" }));
    await user.type(
      screen.getByPlaceholderText("Message the people in this session"),
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

  it("returns focus to the agent composer on Escape without writing into it", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [], shared: true }));
    render(
      <>
        <textarea aria-label="Message the agent" defaultValue="keep me" />
        <SessionChatColumn conversationId={SESSION} />
      </>,
    );
    const composer = screen.getByRole("textbox", { name: "Message the agent" });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Session chat" }));
    const draft = screen.getByPlaceholderText("Message the people in this session");
    await user.type(draft, "not for the agent");
    await user.keyboard("{Escape}");

    expect(screen.queryByTestId("session-human-chat-column")).toBeNull();
    expect(composer).toHaveFocus();
    expect(composer).toHaveValue("keep me");
  });
});
