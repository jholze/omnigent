import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type * as ComposerSendShortcutPreferences from "@/lib/composerSendShortcutPreferences";

import { ChatComposerCard } from "./ChatComposerCard";
import { teamChatQueryKeys, type ChannelMember, type DirectoryEntry } from "./chatApi";
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

const ANNA = "anna@team-chat.test";
const JENS = "jens@team-chat.test";
const BOB = "bob@team-chat.test";

/** Card with members already cached, so no members request runs. */
function renderMentionCard() {
  const client = new QueryClient();
  const members: ChannelMember[] = [
    { principal_id: ANNA, kind: "human", display: "Anna" },
    { principal_id: JENS, kind: "human", display: "Jens" },
    { principal_id: BOB, kind: "human", display: "Bob" },
  ];
  client.setQueryData(teamChatQueryKeys.members("c-general"), members);
  const directory = new Map<string, DirectoryEntry>(
    members.map((member) => [
      member.principal_id,
      { id: member.principal_id, kind: "user", display_name: member.display },
    ]),
  );
  render(
    <QueryClientProvider client={client}>
      <ChatComposerCard
        channelId="c-general"
        me={ANNA}
        placeholder="Nachricht an #general"
        label="Nachricht an #general"
        directory={directory}
      />
    </QueryClientProvider>,
  );
  return screen.getByTestId(CHAT_TEST_IDS.composerInput) as HTMLTextAreaElement;
}

function optionLabels(): string[] {
  return screen.queryAllByTestId(CHAT_TEST_IDS.mentionOption).map((row) => row.textContent ?? "");
}

const GROK = "grok@team-chat.test";

/** Directory agent whose owner is the human Jens. */
function renderAgentMentionCard(agentDisplayName: string) {
  const client = new QueryClient();
  const members: ChannelMember[] = [
    { principal_id: ANNA, kind: "human", display: "Anna" },
    { principal_id: JENS, kind: "human", display: "Jens" },
  ];
  client.setQueryData(teamChatQueryKeys.members("c-general"), members);
  const directory = new Map<string, DirectoryEntry>([
    [ANNA, { id: ANNA, kind: "user", display_name: "Anna" }],
    [JENS, { id: JENS, kind: "user", display_name: "Jens" }],
    [
      GROK,
      {
        id: GROK,
        kind: "agent_account",
        display_name: agentDisplayName,
        owner_id: JENS,
      },
    ],
  ]);
  render(
    <QueryClientProvider client={client}>
      <ChatComposerCard
        channelId="c-general"
        me={ANNA}
        placeholder="Nachricht an #general"
        label="Nachricht an #general"
        directory={directory}
      />
    </QueryClientProvider>,
  );
  return screen.getByTestId(CHAT_TEST_IDS.composerInput) as HTMLTextAreaElement;
}

function agentAccountOptionText(): string {
  const row = screen
    .getAllByTestId(CHAT_TEST_IDS.mentionOption)
    .find((option) => option.getAttribute("data-mention-kind") === "agent_account");
  if (!row) throw new Error("agent_account mention option missing");
  return row.textContent ?? "";
}

function chipTokens(): (string | null)[] {
  return screen
    .queryAllByTestId(CHAT_TEST_IDS.composerMentionChip)
    .map((chip) => chip.getAttribute("data-mention-token"));
}

describe("ChatComposerCard mentions", () => {
  it("AC-026c the list is in the document on the same turn as the @ keystroke", () => {
    const input = renderMentionCard();
    fireEvent.change(input, { target: { value: "@" } });
    expect(screen.getByTestId(CHAT_TEST_IDS.mentionList)).toBeInTheDocument();
  });

  it("AC-026c Anna types @je and sees Jens only", () => {
    const input = renderMentionCard();
    fireEvent.change(input, { target: { value: "@je" } });
    expect(optionLabels()).toEqual(["Jens"]);
  });

  it("AC-026e @zz shows Niemand gefunden and Enter inserts no chip and does not send", () => {
    const input = renderMentionCard();
    fireEvent.change(input, { target: { value: "@zz" } });
    expect(screen.getByTestId(CHAT_TEST_IDS.mentionEmpty)).toHaveTextContent("Niemand gefunden");
    expect(optionLabels()).toEqual([]);

    fireEvent.keyDown(input, { key: "Enter" });
    expect(chipTokens()).toEqual([]);
    expect(sendChatMessageMock).not.toHaveBeenCalled();
  });

  it("AC-026f ArrowDown then Enter inserts the highlighted chip and does not send", () => {
    const input = renderMentionCard();
    fireEvent.change(input, { target: { value: "@" } });
    const rows = screen.getAllByTestId(CHAT_TEST_IDS.mentionOption);
    expect(rows.map((row) => row.getAttribute("data-mention-id"))).toEqual(["agent", BOB, JENS]);
    expect(rows[0]).toHaveAttribute("aria-selected", "true");
    expect(rows[1]).toHaveAttribute("aria-selected", "false");
    expect(rows[2]).toHaveAttribute("aria-selected", "false");
    // Selected-row meta text switches to text-foreground to clear 4.5:1 on bg-muted.
    expect(rows[0]).toHaveClass("group");
    const agentMeta = within(rows[0]).getByText("Omnigent-Agent");
    expect(agentMeta).toHaveClass("text-muted-foreground", "group-aria-selected:text-foreground");

    fireEvent.keyDown(input, { key: "ArrowDown" });
    const movedRows = screen.getAllByTestId(CHAT_TEST_IDS.mentionOption);
    expect(movedRows[1]).toHaveAttribute("aria-selected", "true");
    // The agent row is no longer selected, so its subtitle stays muted.
    expect(movedRows[0]).toHaveAttribute("aria-selected", "false");
    expect(within(movedRows[0]).getByText("Omnigent-Agent")).toHaveClass("text-muted-foreground");
    fireEvent.keyDown(input, { key: "Enter" });

    expect(chipTokens()).toEqual([`<@user:${BOB}>`]);
    expect(input.value).toBe("@Bob ");
    expect(screen.queryByTestId(CHAT_TEST_IDS.mentionList)).not.toBeInTheDocument();
    expect(sendChatMessageMock).not.toHaveBeenCalled();
  });

  it("AC-026f Escape closes the list without inserting a chip", () => {
    const input = renderMentionCard();
    fireEvent.change(input, { target: { value: "@je" } });
    expect(screen.getByTestId(CHAT_TEST_IDS.mentionList)).toBeInTheDocument();

    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByTestId(CHAT_TEST_IDS.mentionList)).not.toBeInTheDocument();
    expect(chipTokens()).toEqual([]);
    expect(input.value).toBe("@je");
  });

  it("AC-026g send serializes chips to tokens and keeps the rest literal", () => {
    const input = renderMentionCard();
    fireEvent.change(input, { target: { value: "@je" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.change(input, { target: { value: "@Jens frag @ag" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(chipTokens()).toEqual([`<@user:${JENS}>`, "<@omnigent_agent:agent>"]);
    expect(sendChatMessageMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Senden" }));
    expect(sendChatMessageMock).toHaveBeenCalledTimes(1);
    const [, sent] = sendChatMessageMock.mock.calls[0] as unknown as [unknown, { body: string }];
    expect(sent.body.trim()).toBe(`<@user:${JENS}> frag <@omnigent_agent:agent>`);
    expect(chipTokens()).toEqual([]);
  });

  it("FR-026 an agent account shows its owner once", () => {
    const suffixed = renderAgentMentionCard("Grok (Jens)");
    fireEvent.change(suffixed, { target: { value: "@" } });
    const suffixedText = agentAccountOptionText();
    expect(suffixedText).toContain("Grok (Jens)");
    expect(suffixedText).not.toContain("Grok (Jens) (Jens)");

    cleanup();

    const raw = renderAgentMentionCard("Grok");
    fireEvent.change(raw, { target: { value: "@" } });
    const rawText = agentAccountOptionText();
    expect(rawText).toContain("Grok (Jens)");
    expect(rawText.match(/\(Jens\)/g)).toEqual(["(Jens)"]);
  });
});
