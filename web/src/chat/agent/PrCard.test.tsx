// Read-only PR card in an open Agent-Thread (FR-060). `usePullRequestInfo`
// is mocked, so no request leaves the test; the normaliser and the panel
// state machine are the real ones the right-rail tab uses.
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type * as PullRequestsModule from "@/hooks/usePullRequests";
import type { PullRequest, PullRequestInfo } from "@/hooks/usePullRequests";

const prInfo = vi.hoisted(() => ({
  current: { isLoading: false, error: null, data: undefined } as {
    isLoading: boolean;
    error: unknown;
    data: unknown;
  },
}));
const usePullRequestInfoMock = vi.hoisted(() => vi.fn((_id: string | undefined) => prInfo.current));
const fetchMock = vi.hoisted(() => vi.fn());

vi.mock("@/hooks/usePullRequests", async (importOriginal) => ({
  ...(await importOriginal<typeof PullRequestsModule>()),
  usePullRequestInfo: (id: string | undefined) => usePullRequestInfoMock(id),
}));

import { PR_CARD_TEST_ID, PrCard, PrCardView, prCiStatus, prStateLabel } from "./PrCard";

const SESSION = "conv_agent";
const PR_URL = "https://github.com/jholze/omnigent/pull/42";

function pullRequest(overrides: Partial<PullRequest> = {}): PullRequest {
  return {
    number: 42,
    title: "Fix billing rounding",
    state: "OPEN",
    url: PR_URL,
    is_draft: false,
    author: "anna",
    base_ref: "main",
    head_ref: "fix/billing",
    checks: { passing: 5, failing: 0, pending: 0, total: 5, runs: [] },
    ...overrides,
  };
}

function info(overrides: Partial<PullRequestInfo> = {}): PullRequestInfo {
  return {
    object: "session.github.info",
    available: true,
    provider: "github",
    auth: {
      authenticated: true,
      hint: null,
      cli: { name: "gh", available: true },
      accounts: null,
      selected_account: "anna",
    },
    branch: "fix/billing",
    repo: { name_with_owner: "jholze/omnigent" },
    base_ref: "main",
    pr: pullRequest(),
    ...overrides,
  };
}

function answer(data: PullRequestInfo | undefined, error: unknown = null) {
  prInfo.current = { isLoading: false, error, data };
}

beforeEach(() => {
  usePullRequestInfoMock.mockClear();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  answer(info());
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("PrCardView", () => {
  it("AC-060a PR #42 open with passing checks shows #42, state open, CI grün and a link", () => {
    render(<PrCardView pr={pullRequest()} />);
    const card = screen.getByTestId(PR_CARD_TEST_ID);
    expect(card).toHaveAttribute("data-state", "open");
    expect(card).toHaveAttribute("data-ci", "passing");
    expect(within(card).getByText("#42")).toBeInTheDocument();
    expect(within(card).getByText("open")).toBeInTheDocument();
    expect(within(card).getByText("grün")).toBeInTheDocument();
    const link = within(card).getByRole("link", { name: /Fix billing rounding/ });
    expect(link).toHaveAttribute("href", PR_URL);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(within(card).queryByRole("button")).toBeNull();
  });

  it("AC-060a the CI summary is red on any failing check, then running, then green", () => {
    const checks = { passing: 3, failing: 0, pending: 0, total: 3, runs: [] };
    expect(prCiStatus({ checks }).label).toBe("grün");
    expect(prCiStatus({ checks: { ...checks, pending: 1 } }).label).toBe("läuft");
    expect(prCiStatus({ checks: { ...checks, pending: 1, failing: 1 } }).label).toBe("rot");
    expect(prStateLabel({ state: "OPEN", is_draft: false })).toBe("open");
    expect(prStateLabel({ state: "OPEN", is_draft: true })).toBe("draft");
    expect(prStateLabel({ state: "MERGED", is_draft: false })).toBe("merged");
  });
});

describe("PrCard", () => {
  it("AC-060a a session with an attached PR shows the card from the session's PR info", () => {
    render(<PrCard sessionId={SESSION} workspace="/work/billing" />);
    expect(usePullRequestInfoMock).toHaveBeenCalledWith(SESSION);
    const card = screen.getByTestId(PR_CARD_TEST_ID);
    expect(within(card).getByText("#42")).toBeInTheDocument();
    expect(within(card).getByText("grün")).toBeInTheDocument();
  });

  it.each([
    [
      "the GitHub app is not signed in",
      info({
        auth: {
          authenticated: false,
          hint: null,
          cli: { name: "gh", available: true },
          accounts: null,
          selected_account: null,
        },
        repo: null,
        pr: null,
      }),
    ],
    [
      "the provider CLI is missing",
      info({
        auth: {
          authenticated: false,
          hint: null,
          cli: { name: "gh", available: false },
          accounts: null,
          selected_account: null,
        },
        pr: null,
      }),
    ],
    [
      "no provider serves the workspace",
      info({ available: false, reason: "provider_unavailable", pr: null }),
    ],
  ])("AC-060b %s: no PR card and no error", (_case, data) => {
    answer(data);
    const view = render(<PrCard sessionId={SESSION} workspace="/work/billing" />);
    expect(screen.queryByTestId(PR_CARD_TEST_ID)).toBeNull();
    expect(view.container).toBeEmptyDOMElement();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("AC-060b a failed PR request renders nothing and no error text", () => {
    answer(undefined, new Error("gh: not authenticated"));
    const view = render(<PrCard sessionId={SESSION} workspace="/work/billing" />);
    expect(view.container).toBeEmptyDOMElement();
    expect(document.body.textContent).not.toContain("not authenticated");
  });

  it("AC-060b a session without a workspace does not ask for PR info", () => {
    const view = render(<PrCard sessionId={SESSION} workspace={null} />);
    expect(view.container).toBeEmptyDOMElement();
    expect(usePullRequestInfoMock).toHaveBeenCalledWith(undefined);
  });
});
