import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type * as MotionReact from "motion/react";

import type { ChatChannel } from "./chatApi";
import { ChatSidebar, isTeamChatPathname } from "./ChatSidebar";
import type * as MotionRootModule from "./MotionRoot";
import { CHAT_TEST_IDS } from "./testids";

const reducedMotion = vi.hoisted(() => ({ value: false }));
const animateSpy = vi.hoisted(() => vi.fn());

vi.mock("./MotionRoot", async (importOriginal) => ({
  ...(await importOriginal<typeof MotionRootModule>()),
  useChatReducedMotion: () => reducedMotion.value,
}));
vi.mock("motion/react", async (importOriginal) => {
  const actual = await importOriginal<typeof MotionReact>();
  return {
    ...actual,
    useAnimate: () => {
      const [scope] = actual.useAnimate();
      return [scope, animateSpy];
    },
  };
});

beforeEach(() => {
  reducedMotion.value = false;
  animateSpy.mockReset();
});
afterEach(() => cleanup());

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

  it("keeps the badges decorative and caps large counts at 99+", () => {
    render(
      <MemoryRouter>
        <ChatSidebar
          channels={[
            channel({ id: "c-general", name: "general", unread_count: 250, mention_count: 120 }),
          ]}
          dms={[]}
        />
      </MemoryRouter>,
    );
    const general = screen.getByRole("link", { name: "#general, 250 ungelesen, 120 Erwähnungen" });
    const unread = within(general).getByTestId(CHAT_TEST_IDS.unreadBadge);
    const mention = within(general).getByTestId(CHAT_TEST_IDS.mentionBadge);
    expect(unread).toHaveTextContent(/^99\+$/);
    expect(mention).toHaveTextContent(/^@99\+$/);
    for (const badge of [unread, mention]) {
      expect(badge).toHaveAttribute("aria-hidden", "true");
      expect(badge).not.toHaveAttribute("aria-label");
    }
  });

  it("FR-078 pulses the unread and mention badges when the counts change after first paint", () => {
    const sidebar = (unread: number, mentions: number) => (
      <MemoryRouter>
        <ChatSidebar
          channels={[
            channel({
              id: "c-general",
              name: "general",
              unread_count: unread,
              mention_count: mentions,
            }),
          ]}
          dms={[]}
        />
      </MemoryRouter>
    );
    const { rerender } = render(sidebar(2, 1));
    expect(animateSpy).not.toHaveBeenCalled();

    rerender(sidebar(3, 1));
    expect(animateSpy).toHaveBeenCalledTimes(1);
    const unread = screen.getByTestId(CHAT_TEST_IDS.unreadBadge);
    const [el, keyframes, transition] = animateSpy.mock.calls[0];
    expect(el).toBe(unread);
    expect(keyframes).toEqual({ transform: ["scale(1)", "scale(1.08)", "scale(1)"] });
    expect(transition.duration).toBeCloseTo(0.15);
    expect(unread.dataset.motion).toBe("badge-pulse");

    rerender(sidebar(3, 2));
    expect(animateSpy).toHaveBeenCalledTimes(2);
    expect(animateSpy.mock.calls[1][0]).toBe(screen.getByTestId(CHAT_TEST_IDS.mentionBadge));
  });

  it("AC-079a reduced motion: sidebar badges do not pulse", () => {
    reducedMotion.value = true;
    const sidebar = (unread: number) => (
      <MemoryRouter>
        <ChatSidebar
          channels={[
            channel({ id: "c-general", name: "general", unread_count: unread, mention_count: 1 }),
          ]}
          dms={[]}
        />
      </MemoryRouter>
    );
    const { rerender } = render(sidebar(2));
    rerender(sidebar(5));
    expect(animateSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId(CHAT_TEST_IDS.unreadBadge).dataset.motion).toBe(
      "badge-pulse-reduced",
    );
    expect(screen.getByTestId(CHAT_TEST_IDS.mentionBadge).dataset.motion).toBe(
      "badge-pulse-reduced",
    );
  });
});
