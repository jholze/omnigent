// Scroll anchoring for streamed agent output in the message list (FR-080).
// jsdom does no layout: the scroller's geometry and the footer height are
// stubbed per element, and ResizeObserver callbacks run when the test says so.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DirectoryEntry } from "./chatApi";
import type { MessageView } from "./MessageItem";
import { MessageList } from "./MessageList";
import { CHAT_TEST_IDS } from "./testids";

const directory = new Map<string, DirectoryEntry>([
  ["anna", { id: "anna", kind: "user", display_name: "Anna" }],
]);

const messages: MessageView[] = [
  {
    key: "m1",
    id: "m1",
    clientId: null,
    channelId: "c1",
    threadRootId: null,
    authorId: "anna",
    authorKind: "user",
    createdAtMs: Date.now(),
    body: "Bitte Tests laufen lassen",
    status: "sent",
  },
];

let observers: { callback: ResizeObserverCallback; instance: ResizeObserver }[];

function triggerResize() {
  act(() => {
    for (const { callback, instance } of observers) callback([], instance);
  });
}

interface Geometry {
  scrollHeight: number;
  clientHeight: number;
  scrollTop: number;
  footerHeight: number;
}

/** Back the scroller and footer box with test-owned layout numbers. */
function stubLayout(scroller: HTMLElement, footerBox: HTMLElement, geometry: Geometry) {
  Object.defineProperty(scroller, "scrollHeight", { get: () => geometry.scrollHeight });
  Object.defineProperty(scroller, "clientHeight", { get: () => geometry.clientHeight });
  Object.defineProperty(scroller, "scrollTop", {
    get: () => geometry.scrollTop,
    set: (value: number) => {
      geometry.scrollTop = Math.max(
        0,
        Math.min(value, geometry.scrollHeight - geometry.clientHeight),
      );
    },
  });
  Object.defineProperty(footerBox, "offsetHeight", { get: () => geometry.footerHeight });
}

function renderList(footerText: string) {
  const list = (text: string) => (
    <MemoryRouter>
      <MessageList
        messages={messages}
        directory={directory}
        me="jens"
        label="Nachrichten in #general"
        onRetry={() => {}}
        footer={<p data-testid="live-footer">{text}</p>}
      />
    </MemoryRouter>
  );
  const view = render(list(footerText));
  const scroller = screen.getByTestId(CHAT_TEST_IDS.messageList);
  const footerBox = screen.getByTestId("live-footer").parentElement!;
  return { scroller, footerBox, rerenderFooter: (text: string) => view.rerender(list(text)) };
}

beforeEach(() => {
  observers = [];
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: ResizeObserverCallback) {
        observers.push({ callback, instance: this as unknown as ResizeObserver });
      }
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    },
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("MessageList streamed footer growth", () => {
  it("AC-080a scrolled up: footer growth shows the pill and keeps scrollTop; a click jumps to the bottom", () => {
    const { scroller, footerBox, rerenderFooter } = renderList("Tests ");
    const geometry = { scrollHeight: 1000, clientHeight: 400, scrollTop: 200, footerHeight: 0 };
    stubLayout(scroller, footerBox, geometry);
    fireEvent.scroll(scroller);

    rerenderFooter("Tests sind grün");
    geometry.scrollHeight = 1100;
    geometry.footerHeight = 100;
    triggerResize();

    const pill = screen.getByTestId(CHAT_TEST_IDS.newMessages);
    expect(pill).toHaveTextContent("Neue Nachrichten");
    expect(Math.abs(scroller.scrollTop - 200)).toBeLessThanOrEqual(2);

    fireEvent.click(pill);

    expect(geometry.scrollHeight - geometry.scrollTop - geometry.clientHeight).toBeLessThanOrEqual(
      2,
    );
    expect(screen.queryByTestId(CHAT_TEST_IDS.newMessages)).toBeNull();
  });

  it("AC-080b at the bottom: footer growth keeps the view at the bottom and shows no pill", () => {
    const { scroller, footerBox, rerenderFooter } = renderList("Tests ");
    const geometry = { scrollHeight: 1000, clientHeight: 400, scrollTop: 600, footerHeight: 0 };
    stubLayout(scroller, footerBox, geometry);
    fireEvent.scroll(scroller);

    rerenderFooter("Tests sind grün");
    geometry.scrollHeight = 1100;
    geometry.footerHeight = 100;
    triggerResize();

    expect(geometry.scrollHeight - geometry.scrollTop - geometry.clientHeight).toBeLessThanOrEqual(
      2,
    );
    expect(screen.queryByTestId(CHAT_TEST_IDS.newMessages)).toBeNull();
  });
});
