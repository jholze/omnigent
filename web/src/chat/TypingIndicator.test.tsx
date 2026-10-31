import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { DirectoryEntry } from "./chatApi";
import { CHAT_TEST_IDS } from "./testids";
import { TypingIndicator } from "./TypingIndicator";
import { applyTypingFrame, resetTypingStore } from "./typingStore";

const ANNA = "anna@team-chat.test";
const JENS = "jens@team-chat.test";

function typing(principalId: string, active = true): void {
  act(() =>
    applyTypingFrame({
      type: "chat",
      kind: "typing",
      channel_id: "c-general",
      principal_id: principalId,
      active,
    }),
  );
}

function renderIndicator(directory = new Map<string, DirectoryEntry>()) {
  render(<TypingIndicator channelId="c-general" me={JENS} directory={directory} />);
}

afterEach(() => {
  cleanup();
  resetTypingStore();
});

describe("TypingIndicator", () => {
  it("AC-032a shows '{name} schreibt…' for another member's typing frame", () => {
    renderIndicator(new Map([[ANNA, { id: ANNA, kind: "user", display_name: "Anna Beispiel" }]]));
    typing(ANNA);

    expect(screen.getByTestId(CHAT_TEST_IDS.typingIndicator)).toHaveTextContent(
      "Anna Beispiel schreibt…",
    );
  });

  it("AC-032a without a directory name the label falls back to the principal id", () => {
    renderIndicator();
    typing(ANNA);

    expect(screen.getByTestId(CHAT_TEST_IDS.typingIndicator)).toHaveTextContent("Anna schreibt…");
  });

  it("AC-032a is hidden when only the viewer is typing", () => {
    renderIndicator();
    typing(JENS);

    expect(screen.queryByTestId(CHAT_TEST_IDS.typingIndicator)).not.toBeInTheDocument();
  });

  it("AC-032a disappears on active:false", () => {
    renderIndicator();
    typing(ANNA);
    typing(ANNA, false);

    expect(screen.queryByTestId(CHAT_TEST_IDS.typingIndicator)).not.toBeInTheDocument();
  });
});
