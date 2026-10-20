import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type * as ComposerSendShortcutPreferences from "@/lib/composerSendShortcutPreferences";

import { ChatComposerCard } from "./ChatComposerCard";
import { CHAT_TEST_IDS } from "./testids";

const sendChatMessageMock = vi.hoisted(() => vi.fn(() => true));
const isComposerSendKeySpy = vi.hoisted(() => ({ calls: [] as boolean[] }));
const viewport = vi.hoisted(() => ({ mobile: false }));

vi.mock("./chatOutbox", () => ({
  sendChatMessage: (...args: unknown[]) => sendChatMessageMock(...(args as [])),
}));

vi.mock("@/hooks/useIsMobileViewport", () => ({
  useIsMobileViewport: () => viewport.mobile,
}));

// Wrap the real key rule so the test proves the card routes Enter through it.
vi.mock("@/lib/composerSendShortcutPreferences", async (importOriginal) => {
  const actual = await importOriginal<typeof ComposerSendShortcutPreferences>();
  return {
    ...actual,
    readSubmitWithModEnter: () => false,
    isComposerSendKey: (...args: Parameters<typeof actual.isComposerSendKey>) => {
      const verdict = actual.isComposerSendKey(...args);
      isComposerSendKeySpy.calls.push(verdict);
      return verdict;
    },
  };
});

function renderCard() {
  const client = new QueryClient();
  render(
    <QueryClientProvider client={client}>
      <ChatComposerCard
        channelId="c-general"
        me="anna"
        placeholder="Nachricht an #general"
        label="Nachricht an #general"
      />
    </QueryClientProvider>,
  );
  return screen.getByTestId(CHAT_TEST_IDS.composerInput) as HTMLTextAreaElement;
}

beforeEach(() => {
  sendChatMessageMock.mockClear();
  isComposerSendKeySpy.calls = [];
  viewport.mobile = false;
});

afterEach(() => cleanup());

describe("ChatComposerCard send key", () => {
  it("NFR-011 Enter sends and Shift+Enter does not, decided by isComposerSendKey", () => {
    const input = renderCard();
    fireEvent.change(input, { target: { value: "Deploy heute 16 Uhr?" } });

    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(isComposerSendKeySpy.calls).toEqual([false]);
    expect(sendChatMessageMock).not.toHaveBeenCalled();

    fireEvent.keyDown(input, { key: "Enter" });
    expect(isComposerSendKeySpy.calls).toEqual([false, true]);
    expect(sendChatMessageMock).toHaveBeenCalledTimes(1);
    expect(sendChatMessageMock).toHaveBeenCalledWith(expect.anything(), {
      channelId: "c-general",
      threadRootId: null,
      authorId: "anna",
      body: "Deploy heute 16 Uhr?",
    });
  });

  it("NFR-011 on a phone viewport Enter does not send; the Senden button does", () => {
    viewport.mobile = true;
    const input = renderCard();
    fireEvent.change(input, { target: { value: "Hallo" } });

    fireEvent.keyDown(input, { key: "Enter" });
    expect(sendChatMessageMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Senden" }));
    expect(sendChatMessageMock).toHaveBeenCalledTimes(1);
  });
});
