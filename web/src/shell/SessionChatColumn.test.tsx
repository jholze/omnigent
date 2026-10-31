// Session chat column (FR-029): header toggle, GET lines, refusals, POST.
// `authenticatedFetch` is mocked; no real network.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type * as IdentityModule from "@/lib/identity";

const fetchMock = vi.hoisted(() => vi.fn());
// Per-test viewport: desktop unless a test sets `viewport.mobile = true`.
const viewport = vi.hoisted(() => ({ mobile: false }));

vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof IdentityModule>()),
  authenticatedFetch: (...args: unknown[]) => fetchMock(...args),
  getCurrentUserId: () => "anna",
}));
vi.mock("@/hooks/useIsMobileViewport", () => ({
  useIsMobileViewport: () => viewport.mobile,
}));

import type { BootstrapResponse, SessionChatLine } from "@/chat/chatApi";
import { CHAT_TEST_IDS } from "@/chat/testids";
import { SessionChatToggle } from "./SessionChatColumn";

const SESSION = "sess-1";
const CHAT_URL = `/v1/sessions/${SESSION}/chat`;

const BOOTSTRAP: BootstrapResponse = {
  me: "anna",
  channels: [],
  dms: [],
  directory: [
    { id: "anna", kind: "user", display_name: "Anna" },
    { id: "jens", kind: "user", display_name: "Jens" },
  ],
  changes_cursor: `1:${"0".repeat(32)}`,
};

function line(id: string, createdBy: string, body: string): SessionChatLine {
  return { id, created_by: createdBy, created_at: 1_700_000_000_000_000, body };
}

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(),
    json: async () => body,
  };
}

/** Route GET/POST of the session chat and the bootstrap; `chatStatus` refuses the GET. */
function serve({ chatStatus = 200, items = [] as SessionChatLine[] } = {}) {
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url === "/v1/chat/bootstrap") return jsonResponse(BOOTSTRAP);
    if (url === CHAT_URL && init?.method === "POST") {
      const { body } = JSON.parse(String(init.body)) as { body: string };
      return jsonResponse(line("line-new", "anna", body), 201);
    }
    if (url === CHAT_URL) {
      return chatStatus === 200
        ? jsonResponse({ items, shared: true })
        : jsonResponse({ detail: "refused" }, chatStatus);
    }
    throw new Error(`unexpected fetch ${url}`);
  });
}

function renderToggle() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TooltipProvider>
          <SessionChatToggle sessionId={SESSION} title="Deploy vorbereiten" />
        </TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function openColumn() {
  fireEvent.click(screen.getByTestId(CHAT_TEST_IDS.sessionColumnToggle));
  return screen.getByTestId(CHAT_TEST_IDS.sessionColumn);
}

function chatCalls(method: "GET" | "POST") {
  return fetchMock.mock.calls.filter(
    ([url, init]) =>
      url === CHAT_URL && ((init as RequestInit | undefined)?.method ?? "GET") === method,
  );
}

beforeEach(() => {
  fetchMock.mockReset();
  viewport.mobile = false;
});

afterEach(() => cleanup());

describe("SessionChatToggle / SessionChatColumn", () => {
  it("AC-029a the header toggle opens the column and loads nothing while closed", () => {
    serve();
    renderToggle();
    const toggle = screen.getByTestId(CHAT_TEST_IDS.sessionColumnToggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId(CHAT_TEST_IDS.sessionColumn)).toBeNull();
    expect(chatCalls("GET")).toHaveLength(0);

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId(CHAT_TEST_IDS.sessionColumn)).toBeInTheDocument();
  });

  it("AC-029a the open column renders the lines from GET /v1/sessions/{id}/chat", async () => {
    serve({
      items: [line("l1", "jens", "Schaut ihr kurz drauf?"), line("l2", "anna", "Ja, gleich.")],
    });
    renderToggle();
    const column = openColumn();

    expect(await within(column).findByText("Schaut ihr kurz drauf?")).toBeInTheDocument();
    expect(within(column).getByText("Ja, gleich.")).toBeInTheDocument();
    expect(within(column).getAllByTestId(CHAT_TEST_IDS.message)).toHaveLength(2);
    expect(chatCalls("GET")).toHaveLength(1);
  });

  it("AC-029a the composer posts the trimmed body and the stored line stays in the list", async () => {
    serve();
    renderToggle();
    const column = openColumn();
    const input = await within(column).findByTestId(CHAT_TEST_IDS.composerInput);

    fireEvent.change(input, { target: { value: "  Bitte nicht mergen  " } });
    fireEvent.click(within(column).getByRole("button", { name: "Senden" }));

    await waitFor(() => expect(chatCalls("POST")).toHaveLength(1));
    const [, init] = chatCalls("POST")[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ body: "Bitte nicht mergen" });
    expect(await within(column).findByText("Bitte nicht mergen")).toBeInTheDocument();
    expect(input).toHaveValue("");
    // Session chat has no channel: no member lookup for mentions.
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/members"))).toBe(false);
  });

  it("AC-029c a 404 from the session-chat route shows the error and no composer", async () => {
    serve({ chatStatus: 404 });
    renderToggle();
    const column = openColumn();

    const error = await within(column).findByTestId(CHAT_TEST_IDS.sessionColumnError);
    expect(error).toHaveTextContent("Für diese Session gibt es keinen Session-Chat.");
    expect(within(column).queryByTestId(CHAT_TEST_IDS.composerInput)).toBeNull();
    expect(within(column).queryByRole("button", { name: "Erneut versuchen" })).toBeNull();
  });

  it("AC-029c a 403 from the session-chat route shows the not-shared error", async () => {
    serve({ chatStatus: 403 });
    renderToggle();
    const column = openColumn();

    const error = await within(column).findByTestId(CHAT_TEST_IDS.sessionColumnError);
    expect(error).toHaveTextContent("Der Session-Chat ist für dich hier nicht freigegeben.");
    expect(within(column).queryByTestId(CHAT_TEST_IDS.composerInput)).toBeNull();
  });
});

describe("SessionChatColumn focus (H-1, A11Y-03)", () => {
  function pressEscape() {
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
  }

  it("H-1 A11Y-03 phone: the column opens as a dialog with focus on its heading", async () => {
    viewport.mobile = true;
    serve();
    renderToggle();
    const column = openColumn();

    expect(column).toHaveAttribute("role", "dialog");
    expect(screen.getByRole("dialog")).toBe(column);
    const heading = within(column).getByRole("heading", { name: /Session-Chat/ });
    expect(heading).toHaveAttribute("tabindex", "-1");
    // Heading, not the composer: the soft keyboard stays closed on open.
    await waitFor(() => expect(heading).toHaveFocus());
    expect(column).toContainElement(document.activeElement as HTMLElement);
  });

  it("H-1 A11Y-03 phone: Escape closes the dialog and focus returns to the toggle", async () => {
    viewport.mobile = true;
    serve();
    renderToggle();
    const column = openColumn();
    await waitFor(() => expect(column).toContainElement(document.activeElement as HTMLElement));

    pressEscape();

    await waitFor(() => expect(screen.queryByTestId(CHAT_TEST_IDS.sessionColumn)).toBeNull());
    expect(screen.queryByRole("dialog")).toBeNull();
    const toggle = screen.getByTestId(CHAT_TEST_IDS.sessionColumnToggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await waitFor(() => expect(toggle).toHaveFocus());
  });

  it("H-1 A11Y-03 desktop: the column is not a dialog and focus moves inside it", async () => {
    serve();
    renderToggle();
    const column = openColumn();

    expect(column.tagName).toBe("SECTION");
    expect(column).not.toHaveAttribute("role", "dialog");
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(column).toContainElement(document.activeElement as HTMLElement));
    expect(["TEXTAREA", "H2"]).toContain(document.activeElement?.tagName);
  });

  it("H-1 A11Y-03 desktop: Escape closes the column and focus returns to the toggle", async () => {
    serve();
    renderToggle();
    const column = openColumn();
    await waitFor(() => expect(column).toContainElement(document.activeElement as HTMLElement));

    pressEscape();

    await waitFor(() => expect(screen.queryByTestId(CHAT_TEST_IDS.sessionColumn)).toBeNull());
    const toggle = screen.getByTestId(CHAT_TEST_IDS.sessionColumnToggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await waitFor(() => expect(toggle).toHaveFocus());
  });
});
