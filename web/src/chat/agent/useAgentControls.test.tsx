// Agent controls of an Agent-Thread: slash parsing, the rights matrix and
// the sends behind interrupt / retry / stop / dismiss. The session snapshot
// (`useSession`) and the four endpoints are mocked.
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type * as SessionsApiModule from "@/lib/sessionsApi";
import type * as ChatApiModule from "../chatApi";

const sessionResult = vi.hoisted(() => ({
  current: { session: null, isLoading: false, error: null } as {
    session: { permissionLevel: number | null } | null;
    isLoading: boolean;
    error: Error | null;
  },
}));
const api = vi.hoisted(() => ({
  interruptSession: vi.fn(async (_id: string) => ({})),
  retrySession: vi.fn(async (_id: string) => ({})),
  stopSession: vi.fn(async (_id: string) => ({})),
  dismissAgentThread: vi.fn(async (root: string) => ({
    root_message_id: root,
    agent_state: "dismissed",
  })),
}));

vi.mock("@/hooks/useSession", () => ({ useSession: () => sessionResult.current }));
vi.mock("@/lib/sessionsApi", async (importOriginal) => ({
  ...(await importOriginal<typeof SessionsApiModule>()),
  interrupt: (id: string) => api.interruptSession(id),
  retrySession: (id: string) => api.retrySession(id),
  stopSession: (id: string) => api.stopSession(id),
}));
vi.mock("../chatApi", async (importOriginal) => ({
  ...(await importOriginal<typeof ChatApiModule>()),
  dismissAgentThread: (root: string) => api.dismissAgentThread(root),
}));

import { CHAT_TEST_IDS } from "../testids";
import { AgentControls } from "./AgentControls";
import { useAgentThreadStore } from "./agentThreadStore";
import {
  NO_EDIT_HINT,
  parseAgentCommand,
  useAgentControls,
  type AgentControlsController,
} from "./useAgentControls";

const SESSION = "conv_agent";
const ROOT = "m-root";
const LEVEL_READ = 1;
const LEVEL_EDIT = 2;
const LEVEL_OWNER = 4;

function withLevel(level: number) {
  sessionResult.current = { session: { permissionLevel: level }, isLoading: false, error: null };
}

function renderControls() {
  return renderHook(() =>
    useAgentControls({
      sessionId: SESSION,
      rootId: ROOT,
      directory: new Map(),
      ownerFallback: "Anna",
    }),
  );
}

function expectNothingSent() {
  expect(api.interruptSession).not.toHaveBeenCalled();
  expect(api.retrySession).not.toHaveBeenCalled();
  expect(api.stopSession).not.toHaveBeenCalled();
  expect(api.dismissAgentThread).not.toHaveBeenCalled();
}

/** The toolbar wired to a live controller, as ThreadPane mounts it. */
function ToolbarHarness() {
  const controller = useAgentControls({
    sessionId: SESSION,
    rootId: ROOT,
    directory: new Map(),
    ownerFallback: "Anna",
  });
  return <AgentControls controller={controller} />;
}

function renderToolbar() {
  render(
    <TooltipProvider delayDuration={0}>
      <ToolbarHarness />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  for (const mock of Object.values(api)) mock.mockClear();
  useAgentThreadStore.setState({
    threads: {
      [ROOT]: { channelId: "c-billing", rootId: ROOT, agentState: "active", sessionId: SESSION },
    },
    sessions: {},
    watchers: {},
  });
});

afterEach(cleanup);

describe("parseAgentCommand", () => {
  it("AC-049b /unbekannt and a command with trailing text are normal messages", () => {
    expect(parseAgentCommand("/unbekannt")).toBeNull();
    expect(parseAgentCommand("/interrupt bitte")).toBeNull();
  });

  it("AC-049b the bare command in any case and with blanks is the command", () => {
    expect(parseAgentCommand("/interrupt")).toBe("interrupt");
    expect(parseAgentCommand(" /RETRY ")).toBe("retry");
  });
});

describe("useAgentControls rights matrix", () => {
  it("AC-050a without EDIT retry is blocked with the verbatim hint and nothing is sent", () => {
    withLevel(LEVEL_READ);
    const { result } = renderControls();
    let outcome: ReturnType<AgentControlsController["request"]> | undefined;
    act(() => {
      outcome = result.current.request("retry");
    });
    expect(outcome).toEqual({
      kind: "blocked",
      hint: "Keine Berechtigung: nur Aufrufer und Bearbeiter",
    });
    expect(NO_EDIT_HINT).toBe("Keine Berechtigung: nur Aufrufer und Bearbeiter");
    expectNothingSent();
  });

  it("AC-051a EDIT without OWNER: stop is blocked with the owner hint and nothing is sent", () => {
    withLevel(LEVEL_EDIT);
    const { result } = renderControls();
    let outcome: ReturnType<AgentControlsController["request"]> | undefined;
    act(() => {
      outcome = result.current.request("stop");
    });
    expect(outcome).toEqual({ kind: "blocked", hint: "Nur Anna (Owner) kann stoppen" });
    expect(result.current.stopConfirmOpen).toBe(false);
    expectNothingSent();
  });

  it("AC-051a EDIT without OWNER: the stop button is aria-disabled and its tooltip names the owner", async () => {
    withLevel(LEVEL_EDIT);
    renderToolbar();
    const stop = screen.getByTestId(CHAT_TEST_IDS.agentControlStop);
    expect(stop).toHaveAttribute("aria-disabled", "true");
    act(() => stop.focus());
    await waitFor(() =>
      expect(screen.getByRole("tooltip")).toHaveTextContent("Nur Anna (Owner) kann stoppen"),
    );
    fireEvent.click(stop);
    expect(screen.getByTestId(CHAT_TEST_IDS.agentControlHint)).toHaveTextContent(
      "Nur Anna (Owner) kann stoppen",
    );
    expect(screen.queryByTestId(CHAT_TEST_IDS.agentStopConfirm)).toBeNull();
    expectNothingSent();
  });

  it("AC-051b OWNER: stop asks for confirmation first, then sends stop_session exactly once", async () => {
    withLevel(LEVEL_OWNER);
    renderToolbar();
    fireEvent.click(screen.getByTestId(CHAT_TEST_IDS.agentControlStop));
    expect(await screen.findByTestId(CHAT_TEST_IDS.agentStopConfirm)).toBeInTheDocument();
    expect(api.stopSession).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId(CHAT_TEST_IDS.agentStopConfirmButton));
    await waitFor(() => expect(api.stopSession).toHaveBeenCalledTimes(1));
    expect(api.stopSession).toHaveBeenCalledWith(SESSION);
    expect(api.interruptSession).not.toHaveBeenCalled();
  });

  it("AC-049a OWNER: interrupt sends one interrupt for the session and no second call", async () => {
    withLevel(LEVEL_OWNER);
    const { result } = renderControls();
    let outcome: ReturnType<AgentControlsController["request"]> | undefined;
    act(() => {
      outcome = result.current.request("interrupt");
    });
    expect(outcome).toEqual({ kind: "sent" });
    await waitFor(() => expect(result.current.pending.interrupt).toBe(false));
    expect(api.interruptSession).toHaveBeenCalledTimes(1);
    expect(api.interruptSession).toHaveBeenCalledWith(SESSION);
    expect(api.retrySession).not.toHaveBeenCalled();
    expect(api.stopSession).not.toHaveBeenCalled();
  });

  it("AC-042a dismiss sends one dismiss and stores agent_state dismissed", async () => {
    withLevel(LEVEL_EDIT);
    const { result } = renderControls();
    act(() => {
      result.current.request("dismiss");
    });
    await waitFor(() => expect(result.current.dismissed).toBe(true));
    expect(api.dismissAgentThread).toHaveBeenCalledTimes(1);
    expect(api.dismissAgentThread).toHaveBeenCalledWith(ROOT);
    expect(useAgentThreadStore.getState().threads[ROOT].agentState).toBe("dismissed");
  });

  it("AC-042a after dismissal the toolbar no longer offers Entlassen", async () => {
    withLevel(LEVEL_EDIT);
    renderToolbar();
    fireEvent.click(screen.getByTestId(CHAT_TEST_IDS.agentControlDismiss));
    await waitFor(() => expect(screen.queryByTestId(CHAT_TEST_IDS.agentControlDismiss)).toBeNull());
    expect(api.dismissAgentThread).toHaveBeenCalledTimes(1);
  });

  // Current behaviour: `request` does not consult `dismissed`, so a second
  // `/dismiss` from the composer posts again (the server answers idempotently).
  it("AC-042a a second dismiss request on a dismissed thread is sent again (current code)", async () => {
    withLevel(LEVEL_EDIT);
    const { result } = renderControls();
    act(() => {
      result.current.request("dismiss");
    });
    await waitFor(() => expect(result.current.dismissed).toBe(true));
    await waitFor(() => expect(result.current.pending.dismiss).toBe(false));
    let outcome: ReturnType<AgentControlsController["request"]> | undefined;
    act(() => {
      outcome = result.current.request("dismiss");
    });
    expect(outcome).toEqual({ kind: "sent" });
    await waitFor(() => expect(api.dismissAgentThread).toHaveBeenCalledTimes(2));
  });
});
