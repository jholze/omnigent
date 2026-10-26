import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { ChatChannel } from "./chatApi";
import { ChatSidebar, isTeamChatPathname } from "./ChatSidebar";
import { CHAT_TEST_IDS } from "./testids";

function channel(overrides: Partial<ChatChannel> & Pick<ChatChannel, "id" | "name">): ChatChannel {
  return {
    kind: "channel",
    topic: null,
    project_name: null,
    visibility: "public",
    created_by: "anna",
    created_at: 1,
    unread_count: 0,
    mention_count: 0,
    ...overrides,
  };
}

describe("isTeamChatPathname", () => {
  it("matches the chat shell routes and not the settings section", () => {
    expect(isTeamChatPathname("/chat")).toBe(true);
    expect(isTeamChatPathname("/chat/")).toBe(true);
    expect(isTeamChatPathname("/chat/abc")).toBe(true);
    expect(isTeamChatPathname("/chat/abc/t/root")).toBe(true);
    expect(isTeamChatPathname("/mount/chat/abc", "/mount/chat")).toBe(true);
    expect(isTeamChatPathname("/settings/chat")).toBe(false);
    expect(isTeamChatPathname("/chatroom")).toBe(false);
    expect(isTeamChatPathname("/chat/abc/t")).toBe(false);
  });
});

describe("ChatSidebar", () => {
  it("AC-001a renders #general and keeps Direktnachrichten when there are no DMs", () => {
    render(
      <MemoryRouter>
        <ChatSidebar channels={[channel({ id: "c-general", name: "general" })]} dms={[]} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { name: "#general" })).toBeInTheDocument();
    const dms = screen.getByRole("region", { name: "Direktnachrichten" });
    expect(within(dms).getByRole("heading", { name: "Direktnachrichten" })).toBeVisible();
    expect(within(dms).queryAllByRole("link")).toHaveLength(0);
  });

  it("AC-008a shows unread_count as the badge and mention_count as @n from bootstrap", () => {
    render(
      <MemoryRouter>
        <ChatSidebar
          channels={[
            channel({ id: "c-general", name: "general", unread_count: 3, mention_count: 1 }),
            channel({ id: "c-random", name: "random" }),
          ]}
          dms={[]}
        />
      </MemoryRouter>,
    );
    const general = screen.getByRole("link", { name: "#general, 3 ungelesen, 1 Erwähnung" });
    expect(within(general).getByTestId(CHAT_TEST_IDS.unreadBadge)).toHaveTextContent(/^3$/);
    expect(within(general).getByTestId(CHAT_TEST_IDS.mentionBadge)).toHaveTextContent(/^@1$/);

    const random = screen.getByRole("link", { name: "#random" });
    expect(within(random).queryByTestId(CHAT_TEST_IDS.unreadBadge)).toBeNull();
    expect(within(random).queryByTestId(CHAT_TEST_IDS.mentionBadge)).toBeNull();
  });
});
