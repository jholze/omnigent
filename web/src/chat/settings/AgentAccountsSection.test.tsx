import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { agentAccountActivity, type AgentAccountRecord } from "../chatApi";
import { AgentAccountsSection } from "./AgentAccountsSection";

const fetchMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => fetchMock(...args),
}));

const TOKEN = "ogc_once_token_value";
const SECRET = "whsec_once_secret_value";

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

function account(overrides: Partial<AgentAccountRecord> = {}): AgentAccountRecord {
  return {
    id: "acc-1",
    principal_id: "agent:acc-1",
    owner_id: "jens@bloopark.de",
    display_name: "Grok",
    handle: "grok-jens",
    token_prefix: "once",
    secret_version: 1,
    last_seen_at: null,
    ...overrides,
  };
}

function requestUrl(input: unknown): string {
  return typeof input === "string" ? input : "";
}

function requestMethod(init: unknown): string {
  if (typeof init === "object" && init !== null && "method" in init) {
    const method = (init as { method?: unknown }).method;
    if (typeof method === "string") return method;
  }
  return "GET";
}

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
});

beforeEach(() => {
  fetchMock.mockImplementation(async (input: unknown) => {
    if (requestUrl(input) === "/v1/chat/agent-accounts") return jsonResponse([]);
    return jsonResponse({ detail: "missing" }, 404);
  });
});

describe("agentAccountActivity", () => {
  it("is aktiv inside 10 minutes and inaktiv after that", () => {
    const now = 1_700_000_000_000;
    const windowMs = 10 * 60 * 1000;
    expect(agentAccountActivity((now - windowMs) * 1000, now)).toBe("aktiv");
    expect(agentAccountActivity((now - windowMs - 1000) * 1000, now)).toBe("inaktiv");
    expect(agentAccountActivity(null, now)).toBe("inaktiv");
  });
});

describe("AgentAccountsSection", () => {
  it("AC-061a shows the token and secret once and drops them after dismiss", async () => {
    fetchMock.mockImplementation(async (input: unknown, init?: unknown) => {
      const url = requestUrl(input);
      const method = requestMethod(init);
      if (url === "/v1/chat/agent-accounts" && method === "GET") return jsonResponse([]);
      if (url === "/v1/chat/agent-accounts" && method === "POST") {
        const body = JSON.parse(String((init as { body?: string }).body));
        return jsonResponse(
          {
            account: account({ display_name: body.display_name, handle: body.handle }),
            token: TOKEN,
            webhook_secret: SECRET,
          },
          201,
        );
      }
      return jsonResponse({ detail: "missing" }, 404);
    });

    const view = render(<AgentAccountsSection />);
    expect(screen.getByTestId("agent-accounts-hint")).toHaveTextContent(
      "Der Grok-Bot sollte chat_unread mindestens alle 5 Minuten abfragen.",
    );
    expect(screen.queryByLabelText(/webhook/i)).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Anzeigename"), { target: { value: "Grok" } });
    fireEvent.change(screen.getByLabelText("Handle"), { target: { value: "grok-jens" } });
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));

    expect(
      await screen.findByText("Token und Secret sind nur einmal sichtbar."),
    ).toBeInTheDocument();
    expect(screen.getByTestId("agent-account-token")).toHaveTextContent(TOKEN);
    expect(screen.getByTestId("agent-account-secret")).toHaveTextContent(SECRET);
    expect(screen.getAllByRole("button", { name: "Kopieren" })).toHaveLength(2);

    const post = fetchMock.mock.calls.find(
      (call) =>
        requestUrl(call[0]) === "/v1/chat/agent-accounts" && requestMethod(call[1]) === "POST",
    );
    if (post === undefined) {
      throw new Error("missing create request");
    }
    expect(JSON.parse(String((post[1] as { body?: string }).body))).toEqual({
      display_name: "Grok",
      handle: "grok-jens",
    });

    fireEvent.click(screen.getByRole("button", { name: "Schließen" }));
    await waitFor(() => {
      expect(screen.queryByTestId("agent-account-token")).not.toBeInTheDocument();
    });
    expect(screen.queryByText(TOKEN)).not.toBeInTheDocument();
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument();
    expect(
      screen.queryByText("Token und Secret sind nur einmal sichtbar."),
    ).not.toBeInTheDocument();

    view.rerender(<AgentAccountsSection />);
    expect(screen.queryByText(TOKEN)).not.toBeInTheDocument();
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument();

    cleanup();
    render(<AgentAccountsSection />);
    await screen.findByLabelText("Anzeigename");
    expect(screen.queryByText(TOKEN)).not.toBeInTheDocument();
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument();
  });

  it("rotate shows the new token once and revoke removes the row", async () => {
    const rotated = "ogc_rotated_token";
    const rotatedSecret = "whsec_rotated_secret";
    const row = account({
      last_seen_at: (Date.now() - 3 * 60 * 1000) * 1000,
    });
    fetchMock.mockImplementation(async (input: unknown, init?: unknown) => {
      const url = requestUrl(input);
      const method = requestMethod(init);
      if (url === "/v1/chat/agent-accounts" && method === "GET") return jsonResponse([row]);
      if (url === `/v1/chat/agent-accounts/${row.id}/rotate` && method === "POST") {
        return jsonResponse({
          account: { ...row, token_prefix: "rot", secret_version: 2 },
          token: rotated,
          webhook_secret: rotatedSecret,
        });
      }
      if (url === `/v1/chat/agent-accounts/${row.id}` && method === "DELETE") {
        return jsonResponse(null, 204);
      }
      return jsonResponse({ detail: "missing" }, 404);
    });

    render(<AgentAccountsSection />);
    expect(await screen.findByText("aktiv")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Token rotieren für grok-jens" }));
    expect(await screen.findByTestId("agent-account-token")).toHaveTextContent(rotated);
    expect(screen.getByTestId("agent-account-secret")).toHaveTextContent(rotatedSecret);
    fireEvent.click(screen.getByRole("button", { name: "Schließen" }));
    await waitFor(() => expect(screen.queryByText(rotated)).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Widerrufen grok-jens" }));
    await waitFor(() => expect(screen.queryByTestId("agent-account-row")).not.toBeInTheDocument());
    expect(screen.getByText("Noch keine Agentenkonten.")).toBeInTheDocument();
  });
});
