/**
 * Team-chat frames and the reconnect backfill.
 *
 * Frames carry ids only. After the updates socket drops and reconnects,
 * `GET /v1/chat/changes?after=<cursor>` is merged by id. `reset` refetches
 * bootstrap and replaces the cursor.
 */
import { useEffect } from "react";
import { type QueryClient, useQueryClient } from "@tanstack/react-query";

import {
  ChatApiError,
  fetchBootstrap,
  fetchChannelMessages,
  fetchChanges,
  teamChatQueryKeys,
} from "./chatApi";
import { applyChatFrame, normalizeChangeItem, useTeamChatStore } from "./teamChatStore";
import { applyAgentThreadFrame } from "./agent/agentThreadStore";
import type {
  ChatChangeFrame,
  ChatThreadFrame,
  ChatUpdatesFrame,
} from "@/lib/sessionUpdatesSocket";

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function threadRootId(value: unknown): string | null | undefined {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return value;
  return undefined;
}

/**
 * `thread.updated` carries Agent-Thread state only. The parser rebuilds the
 * frame from known fields, so anything else a sender attached is dropped.
 */
function parseThreadFrame(
  frame: Record<string, unknown>,
  channelId: string,
): ChatThreadFrame | null {
  if (typeof frame.root_message_id !== "string" || frame.root_message_id === "") return null;
  if (typeof frame.agent_state !== "string" || frame.agent_state === "") return null;
  if (!isFiniteNumber(frame.reply_count) || frame.reply_count < 0) return null;
  const session = frame.session_id;
  if (session !== null && session !== undefined && typeof session !== "string") return null;
  return {
    type: "chat",
    kind: "thread.updated",
    channel_id: channelId,
    root_message_id: frame.root_message_id,
    reply_count: frame.reply_count,
    agent_state: frame.agent_state,
    session_id: typeof session === "string" && session !== "" ? session : null,
    bot_streak: isFiniteNumber(frame.bot_streak) ? frame.bot_streak : 0,
  };
}

/** Narrow a socket payload to a body-less chat frame. Unknown kinds are ignored. */
export function parseChatFrame(raw: unknown): ChatUpdatesFrame | null {
  if (typeof raw !== "object" || raw === null) return null;
  const frame = raw as Record<string, unknown>;
  if (frame.type !== "chat") return null;
  if (typeof frame.channel_id !== "string" || frame.channel_id === "") return null;
  if (frame.kind === "thread.updated") return parseThreadFrame(frame, frame.channel_id);
  const thread = threadRootId(frame.thread_root_id);
  if (thread === undefined || !isFiniteNumber(frame.created_at)) return null;
  if (frame.kind === "message.created") {
    if (typeof frame.message_id !== "string" || frame.message_id === "") return null;
    if (typeof frame.author_id !== "string" || frame.author_id === "") return null;
    return {
      type: "chat",
      kind: "message.created",
      channel_id: frame.channel_id,
      message_id: frame.message_id,
      thread_root_id: thread,
      created_at: frame.created_at,
      author_id: frame.author_id,
    };
  }
  if (frame.kind === "read.updated") {
    if (typeof frame.id !== "string" || frame.id === "") return null;
    return {
      type: "chat",
      kind: "read.updated",
      channel_id: frame.channel_id,
      thread_root_id: thread,
      created_at: frame.created_at,
      id: frame.id,
    };
  }
  return null;
}

function cacheChannelMessages(queryClient: QueryClient, channelId: string): void {
  const queryKey = teamChatQueryKeys.messages(channelId);
  void queryClient
    .fetchQuery({
      queryKey,
      queryFn: ({ signal }) => fetchChannelMessages(channelId, signal),
      // The app client keeps pages fresh for 30s. This fill must run every time.
      staleTime: 0,
    })
    .catch((error: unknown) => {
      const status = error instanceof ChatApiError ? String(error.status) : "error";
      console.warn(`[team-chat] message cache fill failed (${status})`);
      // A failed fill does not count as loaded: mark any cached page stale so
      // an open view refetches once now and the next mount asks the server.
      void queryClient.invalidateQueries({ queryKey, exact: true });
    });
}

function cursorAfter(next: string | null): string | null {
  if (next === null || next === "") return null;
  return next;
}

/**
 * Merge one changes page, then the next cursor.
 *
 * A blank or repeated cursor stops. `reset` drops the rest of the feed,
 * refetches bootstrap, and stores that cursor instead.
 */
async function mergeChangesPage(
  queryClient: QueryClient,
  after: string,
  seen: Set<string>,
): Promise<void> {
  if (seen.has(after)) return;
  seen.add(after);
  const page = await fetchChanges(after);
  const normalized = page.items.flatMap((item) => {
    const change = normalizeChangeItem(item);
    return change === null ? [] : [change];
  });
  const added = useTeamChatStore.getState().mergeChanges(normalized);
  useTeamChatStore.getState().noteChannels(added);
  if (page.reset) {
    const fresh = await queryClient.fetchQuery({
      queryKey: teamChatQueryKeys.bootstrap,
      queryFn: ({ signal }) => fetchBootstrap(signal),
      // A cached bootstrap must not replace a cursor paging already advanced.
      staleTime: 0,
    });
    useTeamChatStore.getState().replaceCursor(fresh.changes_cursor);
    useTeamChatStore
      .getState()
      .noteChannels([
        ...fresh.channels.map((channel) => channel.id),
        ...fresh.dms.map((channel) => channel.id),
      ]);
    return;
  }
  const next = cursorAfter(page.next);
  if (next === null || next === after) return;
  useTeamChatStore.getState().replaceCursor(next);
  await mergeChangesPage(queryClient, next, seen);
}

/** Page `GET /v1/chat/changes?after=<cursor>` and merge it by id. */
export function backfillFromCursor(queryClient: QueryClient, cursor: string): Promise<void> {
  return mergeChangesPage(queryClient, cursor, new Set());
}

let backfillInFlight: Promise<void> | null = null;
const framesDuringBackfill: ChatChangeFrame[] = [];
let flushingBackfill = false;

/**
 * Apply a frame now, or after the in-flight changes backfill.
 *
 * A `thread.updated` frame is a state snapshot, not a change id, so it
 * applies at once and never waits for the backfill.
 */
export function acceptChatFrame(frame: ChatUpdatesFrame): void {
  if (frame.kind === "thread.updated") {
    applyAgentThreadFrame(frame);
    return;
  }
  if (backfillInFlight !== null && !flushingBackfill) {
    framesDuringBackfill.push(frame);
    return;
  }
  applyChatFrame(frame);
}

function flushFramesAfterBackfill(): void {
  flushingBackfill = true;
  try {
    for (const frame of framesDuringBackfill.splice(0)) applyChatFrame(frame);
  } finally {
    flushingBackfill = false;
  }
}

/** Start a backfill when a cursor exists. Overlapping calls share one flight. */
export function requestChatBackfill(queryClient: QueryClient): void {
  const cursor = useTeamChatStore.getState().changesCursor;
  if (cursor === null || backfillInFlight !== null) return;
  backfillInFlight = backfillFromCursor(queryClient, cursor)
    .catch((error: unknown) => {
      const status = error instanceof ChatApiError ? String(error.status) : "error";
      console.warn(`[team-chat] changes backfill failed (${status})`);
    })
    .finally(() => {
      backfillInFlight = null;
      flushFramesAfterBackfill();
    });
}

/**
 * After the updates socket drops and opens again, backfill from the cursor.
 * The provider is the only caller. A first connect does not backfill.
 */
export function watchChatSocketReconnect(
  socket: {
    isConnected: () => boolean;
    subscribeStatus: (listener: () => void) => () => void;
  },
  queryClient: QueryClient,
): () => void {
  let connected = socket.isConnected();
  let sawDrop = false;
  return socket.subscribeStatus(() => {
    const now = socket.isConnected();
    if (connected && !now) sawDrop = true;
    if (!connected && now && sawDrop) {
      sawDrop = false;
      requestChatBackfill(queryClient);
    }
    connected = now;
  });
}

/**
 * Fill the message cache for channels that gained a message.
 *
 * Safe under StrictMode: taking the pending list twice is a no-op the second
 * time, and the first fill is already in flight. Does nothing while the flag
 * is off, so `/chat` with the feature disabled never calls `/v1/chat/*`.
 */
export function useChatFrames(enabled: boolean): void {
  const queryClient = useQueryClient();
  const pendingChannels = useTeamChatStore((state) => state.pendingChannels);
  useEffect(() => {
    if (!enabled || pendingChannels.length === 0) return;
    const channelIds = useTeamChatStore.getState().takePendingChannels();
    for (const channelId of channelIds) cacheChannelMessages(queryClient, channelId);
  }, [enabled, pendingChannels, queryClient]);
}
