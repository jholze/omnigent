/**
 * Team-chat frames and the reconnect backfill.
 *
 * Frames carry ids only. After the updates socket drops and reconnects,
 * `GET /v1/chat/changes?after=<cursor>` is merged by id. `reset` refetches
 * bootstrap, replaces the cursor and reloads open views (FR-083).
 *
 * Badge counts live in bootstrap. A new `read.updated` change, or a message
 * from someone else, refetches bootstrap once per short window (FR-008,
 * FR-009), so another tab's read clears the badge here too.
 */
import { useEffect } from "react";
import { type QueryClient, useQueryClient } from "@tanstack/react-query";

import {
  ChatApiError,
  fetchBootstrap,
  fetchChannelMessages,
  fetchChanges,
  teamChatQueryKeys,
  type BootstrapResponse,
} from "./chatApi";
import { fetchLatest } from "./freshQuery";
import {
  applyChatFrame,
  normalizeChangeItem,
  teamChatChangeKey,
  type TeamChatChange,
  useTeamChatStore,
} from "./teamChatStore";
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
  // Cancel an in-flight page first. A fetch started before this message
  // existed must not land last and leave the open DM empty (AC-021a).
  const fill = fetchLatest(queryClient, queryKey, (signal) =>
    fetchChannelMessages(channelId, signal),
  );
  void fill.catch((error: unknown) => {
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
 * Query prefixes an open view renders from. A reset means changes were
 * skipped, so every one of them is stale. Inactive ones refetch on mount.
 */
const OPEN_VIEW_PREFIXES = [
  ["team-chat", "messages"],
  ["team-chat", "thread"],
  ["team-chat", "agent-threads"],
  ["team-chat", "members"],
] as const;

export function reloadOpenChatViews(queryClient: QueryClient): Promise<void> {
  return Promise.all(
    OPEN_VIEW_PREFIXES.map((queryKey) => queryClient.invalidateQueries({ queryKey })),
  ).then(() => undefined);
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
    const fresh = await fetchLatest(queryClient, teamChatQueryKeys.bootstrap, (signal) =>
      fetchBootstrap(signal),
    );
    useTeamChatStore.getState().replaceCursor(fresh.changes_cursor);
    useTeamChatStore
      .getState()
      .noteChannels([
        ...fresh.channels.map((channel) => channel.id),
        ...fresh.dms.map((channel) => channel.id),
      ]);
    await reloadOpenChatViews(queryClient);
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

/** Coalesce badge refreshes. Keeps AC-009a (badge gone within 1 s) with room for the fetch. */
export const BADGE_REFRESH_DELAY_MS = 250;

/**
 * True when this new change can move a listed sidebar count. My own messages
 * do not. A channel bootstrap does not list yet is left to the new-channel
 * refetch in `ChatPage`, which brings its counts along.
 */
export function changeMovesBadges(
  change: TeamChatChange,
  me: string | undefined,
  listed: ReadonlySet<string>,
): boolean {
  if (!listed.has(change.channel_id)) return false;
  if (change.kind === "read") return true;
  if (change.thread_root_id !== null) return false;
  return change.author_id === undefined || me === undefined || change.author_id !== me;
}

/**
 * Refetch bootstrap shortly after a change that can move unread or mention
 * counts. Waits for an in-flight bootstrap so its stale answer is not the
 * last word, then refetches once.
 */
function useBadgeRefresh(enabled: boolean): void {
  const queryClient = useQueryClient();
  useEffect(() => {
    if (!enabled) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const run = (): void => {
      timer = null;
      if (queryClient.isFetching({ queryKey: teamChatQueryKeys.bootstrap, exact: true }) > 0) {
        timer = setTimeout(run, BADGE_REFRESH_DELAY_MS);
        return;
      }
      void queryClient.invalidateQueries(
        { queryKey: teamChatQueryKeys.bootstrap, exact: true },
        { cancelRefetch: false },
      );
    };
    let known = new Set(Object.keys(useTeamChatStore.getState().changes));
    const unsubscribe = useTeamChatStore.subscribe((state, previous) => {
      if (state.changes === previous.changes) return;
      const data = queryClient.getQueryData<BootstrapResponse>(teamChatQueryKeys.bootstrap);
      const listed = new Set([...(data?.channels ?? []), ...(data?.dms ?? [])].map((c) => c.id));
      let moves = false;
      for (const change of Object.values(state.changes)) {
        const key = teamChatChangeKey(change);
        if (known.has(key)) continue;
        if (changeMovesBadges(change, data?.me, listed)) moves = true;
      }
      known = new Set(Object.keys(state.changes));
      if (moves && timer === null) timer = setTimeout(run, BADGE_REFRESH_DELAY_MS);
    });
    return () => {
      unsubscribe();
      if (timer !== null) clearTimeout(timer);
    };
  }, [enabled, queryClient]);
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
  useBadgeRefresh(enabled);
  useEffect(() => {
    if (!enabled || pendingChannels.length === 0) return;
    const channelIds = useTeamChatStore.getState().takePendingChannels();
    for (const channelId of channelIds) cacheChannelMessages(queryClient, channelId);
  }, [enabled, pendingChannels, queryClient]);
}
