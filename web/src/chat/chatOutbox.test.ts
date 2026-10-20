import { QueryClient } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as ChatApi from "./chatApi";
import { sendChatMessage, useChatOutbox } from "./chatOutbox";

const postMessageMock = vi.hoisted(() => vi.fn());

vi.mock("./chatApi", async (importOriginal) => ({
  ...(await importOriginal<typeof ChatApi>()),
  postMessage: (...args: unknown[]) => postMessageMock(...args),
}));

beforeEach(() => {
  postMessageMock.mockReset();
  useChatOutbox.setState({ entries: [] });
});

describe("sendChatMessage", () => {
  it.each([
    ["empty", ""],
    ["spaces", "   "],
    ["newlines and tabs", "\n\t \n"],
  ])("AC-015a does not post a blank or whitespace body (%s)", (_name, body) => {
    const sent = sendChatMessage(new QueryClient(), {
      channelId: "c-general",
      threadRootId: null,
      authorId: "anna",
      body,
    });
    expect(sent).toBe(false);
    expect(postMessageMock).not.toHaveBeenCalled();
    expect(useChatOutbox.getState().entries).toEqual([]);
  });
});
