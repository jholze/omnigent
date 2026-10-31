// Run-stuck banner: the 120 s clock, the banner's two ways out and the
// grant-gated "Session öffnen" link. The session snapshot is mocked.
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type * as SessionsApiModule from "@/lib/sessionsApi";

const sessionResult = vi.hoisted(() => ({
  current: { session: null, isLoading: false, error: null } as {
    session: { permissionLevel: number | null } | null;
    isLoading: boolean;
    error: Error | null;
  },
}));
const interruptMock = vi.hoisted(() => vi.fn(async (_id: string) => ({})));

vi.mock("@/hooks/useSession", () => ({ useSession: () => sessionResult.current }));
vi.mock("@/lib/sessionsApi", async (importOriginal) => ({
  ...(await importOriginal<typeof SessionsApiModule>()),
  interrupt: (id: string) => interruptMock(id),
}));

import { ApiError } from "@/lib/sessionsApi";
import { CHAT_TEST_IDS } from "../testids";
import { RUN_STUCK_MS, RunStuckBanner, useRunStuck } from "./RunStuckBanner";
import { useAgentControls } from "./useAgentControls";

const SESSION = "conv_agent";

function BannerHarness() {
  const controller = useAgentControls({
    sessionId: SESSION,
    rootId: "m-root",
    directory: new Map(),
    ownerFallback: "Anna",
  });
  return <RunStuckBanner sessionId={SESSION} controller={controller} />;
}

function renderBanner() {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <BannerHarness />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  interruptMock.mockClear();
  sessionResult.current = { session: { permissionLevel: 2 }, isLoading: false, error: null };
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("useRunStuck", () => {
  it("AC-053a running with unchanged activity for 120 s turns stuck", () => {
    vi.useFakeTimers();
    const activity = { blocks: 1 };
    const { result } = renderHook(({ running, state }) => useRunStuck(running, state), {
      initialProps: { running: true, state: activity as unknown },
    });
    expect(RUN_STUCK_MS).toBe(120_000);
    act(() => vi.advanceTimersByTime(RUN_STUCK_MS - 1));
    expect(result.current).toBe(false);
    act(() => vi.advanceTimersByTime(1));
    expect(result.current).toBe(true);
  });

  it("AC-053a new activity restarts the 120 s clock", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ running, state }) => useRunStuck(running, state), {
      initialProps: { running: true, state: { blocks: 1 } as unknown },
    });
    act(() => vi.advanceTimersByTime(RUN_STUCK_MS - 1_000));
    rerender({ running: true, state: { blocks: 2 } });
    act(() => vi.advanceTimersByTime(RUN_STUCK_MS - 1));
    expect(result.current).toBe(false);
    act(() => vi.advanceTimersByTime(1));
    expect(result.current).toBe(true);
    // A stream event after the banner showed clears it at once.
    rerender({ running: true, state: { blocks: 3 } });
    expect(result.current).toBe(false);
  });

  it("AC-053a a session that is not running never turns stuck", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useRunStuck(false, {}));
    act(() => vi.advanceTimersByTime(RUN_STUCK_MS * 2));
    expect(result.current).toBe(false);
  });
});

describe("RunStuckBanner", () => {
  it("AC-053a shows Agent reagiert nicht with Unterbrechen and Session öffnen", async () => {
    renderBanner();
    const banner = screen.getByTestId(CHAT_TEST_IDS.runStuckBanner);
    expect(banner).toHaveTextContent("Agent reagiert nicht");
    const interrupt = within(banner).getByTestId(CHAT_TEST_IDS.runStuckInterrupt);
    expect(interrupt).toHaveTextContent("Unterbrechen");
    expect(within(banner).getByTestId(CHAT_TEST_IDS.runStuckOpenSession)).toHaveTextContent(
      "Session öffnen",
    );
    fireEvent.click(interrupt);
    await waitFor(() => expect(interruptMock).toHaveBeenCalledTimes(1));
    expect(interruptMock).toHaveBeenCalledWith(SESSION);
  });

  it("AC-052a with a grant the banner's Session öffnen links to /c/{sessionId}", () => {
    sessionResult.current = { session: { permissionLevel: 1 }, isLoading: false, error: null };
    renderBanner();
    expect(screen.getByTestId(CHAT_TEST_IDS.runStuckOpenSession)).toHaveAttribute(
      "href",
      `/c/${SESSION}`,
    );
  });

  it("AC-052b without a grant the banner renders no Session öffnen link", () => {
    sessionResult.current = {
      session: null,
      isLoading: false,
      error: new ApiError("forbidden", 403, null),
    };
    renderBanner();
    expect(screen.getByTestId(CHAT_TEST_IDS.runStuckBanner)).toBeInTheDocument();
    expect(screen.queryByTestId(CHAT_TEST_IDS.runStuckOpenSession)).toBeNull();
  });
});
