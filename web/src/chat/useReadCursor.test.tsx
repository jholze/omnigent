import { createElement, useRef, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type * as ChatApi from "./chatApi";
import type { ReadCursor } from "./chatApi";
import { CHAT_TEST_IDS } from "./testids";
import { READ_CURSOR_THROTTLE_MS, useReadCursor } from "./useReadCursor";

const postReadCursorMock = vi.hoisted(() => vi.fn());

vi.mock("./chatApi", async (importOriginal) => ({
  ...(await importOriginal<typeof ChatApi>()),
  postReadCursor: (...args: unknown[]) => postReadCursorMock(...args),
}));

/** Observers the hook created; the test decides when a row is on screen. */
const observers = new Set<ControlledObserver>();

class ControlledObserver {
  readonly targets = new Set<Element>();
  private readonly callback: IntersectionObserverCallback;
  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback;
    observers.add(this);
  }
  observe(target: Element) {
    this.targets.add(target);
  }
  unobserve(target: Element) {
    this.targets.delete(target);
  }
  disconnect() {
    this.targets.clear();
    observers.delete(this);
  }
  takeRecords() {
    return [];
  }
  root = null;
  rootMargin = "";
  thresholds = [];
  fire(isIntersecting: boolean) {
    const entries = [...this.targets].map(
      (target) => ({ target, isIntersecting }) as IntersectionObserverEntry,
    );
    this.callback(entries, this as unknown as IntersectionObserver);
  }
}

function setRowOnScreen(isIntersecting: boolean) {
  act(() => {
    for (const observer of [...observers]) observer.fire(isIntersecting);
  });
}

let documentVisibility: DocumentVisibilityState = "visible";

function setDocumentVisibility(next: DocumentVisibilityState) {
  documentVisibility = next;
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

function Harness({ rows, newest }: { rows: ReadCursor[]; newest: ReadCursor | null }) {
  const ref = useRef<HTMLDivElement>(null);
  useReadCursor("c1", newest, ref);
  return (
    <div ref={ref}>
      <ul>
        {rows.map((row) => (
          <li key={row.id} data-testid={CHAT_TEST_IDS.message} data-message-id={row.id} />
        ))}
      </ul>
    </div>
  );
}

const m1: ReadCursor = { id: "m1", created_at: 10 };
const m2: ReadCursor = { id: "m2", created_at: 20 };
const m3: ReadCursor = { id: "m3", created_at: 30 };

function renderCursor(rows: ReadCursor[], newest: ReadCursor | null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children);
  return render(<Harness rows={rows} newest={newest} />, { wrapper });
}

beforeEach(() => {
  vi.useFakeTimers();
  observers.clear();
  postReadCursorMock.mockReset();
  postReadCursorMock.mockResolvedValue(undefined);
  documentVisibility = "visible";
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => documentVisibility,
  });
  vi.stubGlobal("IntersectionObserver", ControlledObserver);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  Reflect.deleteProperty(document, "visibilityState");
});

describe("useReadCursor", () => {
  it("AC-007a posts the newest message as the read cursor once it is visible", async () => {
    renderCursor([m1, m2], m2);
    await advance(0);
    expect(postReadCursorMock).not.toHaveBeenCalled();

    setRowOnScreen(true);
    await advance(0);

    expect(postReadCursorMock).toHaveBeenCalledTimes(1);
    expect(postReadCursorMock).toHaveBeenCalledWith("c1", { id: "m2", created_at: 20 });
  });

  it("AC-007a does not post while the newest message is off screen", async () => {
    renderCursor([m1, m2], m2);
    setRowOnScreen(false);
    await advance(READ_CURSOR_THROTTLE_MS * 2);
    expect(postReadCursorMock).not.toHaveBeenCalled();
  });

  it("AC-007a waits for the document to become visible before posting", async () => {
    documentVisibility = "hidden";
    renderCursor([m1, m2], m2);
    setRowOnScreen(true);
    await advance(READ_CURSOR_THROTTLE_MS * 2);
    expect(postReadCursorMock).not.toHaveBeenCalled();

    setDocumentVisibility("visible");
    await advance(0);
    expect(postReadCursorMock).toHaveBeenCalledTimes(1);
    expect(postReadCursorMock).toHaveBeenCalledWith("c1", { id: "m2", created_at: 20 });
  });

  it("AC-007a posts at most once per second when newer messages arrive", async () => {
    const view = renderCursor([m1, m2], m2);
    setRowOnScreen(true);
    await advance(0);
    expect(postReadCursorMock).toHaveBeenCalledTimes(1);

    view.rerender(<Harness rows={[m1, m2, m3]} newest={m3} />);
    setRowOnScreen(true);
    await advance(READ_CURSOR_THROTTLE_MS - 1);
    expect(postReadCursorMock).toHaveBeenCalledTimes(1);

    await advance(1);
    expect(postReadCursorMock).toHaveBeenCalledTimes(2);
    expect(postReadCursorMock).toHaveBeenLastCalledWith("c1", { id: "m3", created_at: 30 });
  });

  it("AC-007a does not post a cursor that is not newer than the last one sent", async () => {
    const view = renderCursor([m1, m2], m2);
    setRowOnScreen(true);
    await advance(0);
    expect(postReadCursorMock).toHaveBeenCalledTimes(1);

    // The same cursor again after the tab returns.
    setDocumentVisibility("hidden");
    setDocumentVisibility("visible");
    await advance(READ_CURSOR_THROTTLE_MS * 2);
    expect(postReadCursorMock).toHaveBeenCalledTimes(1);

    // An older message becomes the newest one on screen.
    view.rerender(<Harness rows={[m1]} newest={m1} />);
    setRowOnScreen(true);
    await advance(READ_CURSOR_THROTTLE_MS * 2);
    expect(postReadCursorMock).toHaveBeenCalledTimes(1);
  });
});
