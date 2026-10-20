import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import type { DirectoryEntry } from "./chatApi";
import {
  MessageBody,
  MessageItem,
  replyCountLabel,
  splitMessageBody,
  toEpochMs,
  type MessageView,
} from "./MessageItem";
import { CHAT_TEST_IDS } from "./testids";

const directory = new Map<string, DirectoryEntry>([
  ["anna", { id: "anna", kind: "user", display_name: "Anna" }],
  ["bot-1", { id: "bot-1", kind: "agent_account", display_name: null, handle: "grok" }],
]);

function view(overrides: Partial<MessageView> = {}): MessageView {
  return {
    key: "m1",
    id: "m1",
    clientId: null,
    channelId: "c1",
    threadRootId: null,
    authorId: "anna",
    authorKind: "user",
    createdAtMs: Date.now(),
    body: "Hallo",
    status: "sent",
    ...overrides,
  };
}

afterEach(() => cleanup());

describe("splitMessageBody", () => {
  it("AC-020a keeps HTML and Markdown as text and links only http(s)", () => {
    expect(splitMessageBody("<script>alert(1)</script> **fett** https://example.com")).toEqual([
      { type: "text", text: "<script>alert(1)</script> **fett** " },
      { type: "link", href: "https://example.com", text: "https://example.com" },
    ]);
  });

  it("AC-020a never links javascript: or data: URLs", () => {
    const segments = splitMessageBody("javascript:alert(1) data:text/html,<b>x</b>");
    expect(segments.every((segment) => segment.type === "text")).toBe(true);
  });

  it("drops trailing sentence punctuation but keeps a balanced paren", () => {
    expect(splitMessageBody("siehe https://example.com/a.")[1]).toMatchObject({
      href: "https://example.com/a",
    });
    expect(splitMessageBody("(https://de.wikipedia.org/wiki/Foo_(Bar))")[1]).toMatchObject({
      href: "https://de.wikipedia.org/wiki/Foo_(Bar)",
    });
  });

  it("parses mention tokens of the three kinds", () => {
    const segments = splitMessageBody("hi <@user:anna> <@omnigent_agent:coder> <@nope:x>");
    expect(segments.filter((segment) => segment.type === "mention")).toHaveLength(2);
    expect(segments.at(-1)).toEqual({ type: "text", text: " <@nope:x>" });
  });
});

describe("MessageBody", () => {
  it("AC-020a renders a human body as text nodes with a safe link and no script element", () => {
    const { container } = render(
      <MessageBody
        body={"<script>alert(1)</script> **fett** https://example.com javascript:alert(2)"}
        directory={directory}
      />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("strong")).toBeNull();
    expect(container).toHaveTextContent("<script>alert(1)</script> **fett**");
    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "https://example.com");
    expect(links[0]).toHaveAttribute("rel", "noopener noreferrer");
    expect(container).toHaveTextContent("javascript:alert(2)");
  });

  it("renders mention tokens as name chips from the directory", () => {
    render(<MessageBody body="<@user:anna> und <@agent_account:bot-1>" directory={directory} />);
    const chips = screen.getAllByTestId(CHAT_TEST_IDS.mentionChip);
    expect(chips.map((chip) => chip.textContent)).toEqual(["@Anna", "@grok"]);
  });
});

describe("MessageItem", () => {
  it("shows the directory name, falling back to the author id", () => {
    render(
      <MemoryRouter>
        <ul>
          <MessageItem message={view()} directory={directory} />
          <MessageItem
            message={view({ key: "m2", id: "m2", authorId: "bob" })}
            directory={directory}
          />
        </ul>
      </MemoryRouter>,
    );
    const rows = screen.getAllByTestId(CHAT_TEST_IDS.message);
    expect(within(rows[0]).getByText("Anna")).toBeInTheDocument();
    expect(within(rows[1]).getByText("bob")).toBeInTheDocument();
    expect(rows[0].querySelector("time")).not.toBeNull();
  });

  it("shows nicht gesendet with erneut senden on a failed send", () => {
    render(
      <MemoryRouter>
        <ul>
          <MessageItem
            message={view({ id: null, key: "client:x", clientId: "x", status: "failed" })}
            directory={directory}
            onRetry={() => {}}
          />
        </ul>
      </MemoryRouter>,
    );
    expect(screen.getByTestId(CHAT_TEST_IDS.sendFailed)).toHaveTextContent("nicht gesendet");
    expect(screen.getByRole("button", { name: "erneut senden" })).toHaveClass("min-h-11");
  });

  it("links the reply count and the reply action to the thread route", () => {
    render(
      <MemoryRouter>
        <ul>
          <MessageItem
            message={view()}
            directory={directory}
            threadHref="/chat/c1/t/m1"
            replyCount={1}
          />
        </ul>
      </MemoryRouter>,
    );
    const count = screen.getByTestId(CHAT_TEST_IDS.replyCount);
    expect(count).toHaveTextContent("1 Antwort");
    expect(count).toHaveAttribute("href", "/chat/c1/t/m1");
    expect(screen.getByTestId(CHAT_TEST_IDS.replyAction)).toHaveAccessibleName(
      "Im Thread antworten: Nachricht von Anna",
    );
  });
});

describe("helpers", () => {
  it("formats German reply counts", () => {
    expect(replyCountLabel(1)).toBe("1 Antwort");
    expect(replyCountLabel(2)).toBe("2 Antworten");
    expect(replyCountLabel(1284)).toBe("1.284 Antworten");
  });

  it("reads server microseconds", () => {
    expect(toEpochMs(1_760_000_000_000_000)).toBe(1_760_000_000_000);
    expect(toEpochMs(1_760_000_000_000)).toBe(1_760_000_000_000);
  });
});
