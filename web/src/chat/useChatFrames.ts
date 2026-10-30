/**
 * Team-chat frames and the reconnect backfill.
 *
 * Frames carry ids only. After the updates socket drops and reconnects,
 * `GET /v1/chat/changes?after=<cursor>` is merged by id. `reset` refetches
 * bootstrap, replaces the cursor and reloads open views (FR-083).
 *
 * Badge counts arrive with bootstrap. In a channel bootstrap already lists, a
 * new message from someone else or a `read.updated` change updates the
 * cached counts from that channel's message page, never with another
 * bootstrap GET (FR-008, FR-009). Another tab's read clears the badge here
 * too. A channel bootstrap does not list yet is `ChatPage`'s new-channel
 * refetch (AC-021a).
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
  type ChatMessage,
  type MessagePageResponse,
  type ReadCursor,
} from "./chatApi";
import { fetchLatest } from "./freshQuery";
import { isCursorAfter } from "./useReadCursor";
import {
  applyChatFrame,
  normalizeChangeItem,
  teamChatChangeKey,
  type TeamChatChange,
  useTeamChatStore,
} from "./teamChatStore";
import { applyAgentThreadFrame } from "./agent/agentThreadStore";
import { applyTypingFrame, type ChatTypingFrame } from "./typingStore";
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

/**
 * `typing` has no `created_at` or thread. Rebuilt from known fields, so a
 * body or draft a sender attached is dropped (NFR-014).
 */
function parseTypingFrame(
  frame: Record<string, unknown>,
  channelId: string,
): ChatTypingFrame | null {
  if (typeof frame.principal_id !== "string" || frame.principal_id === "") return null;
  if (typeof frame.active !== "boolean") return null;
  return {
    type: "chat",
    kind: "typing",
    channel_id: channelId,
    principal_id: frame.principal_id,
    active: frame.active,
  };
}

/** A parsed chat frame: a change or state frame, or a typing signal. */
export type ParsedChatFrame = ChatUpdatesFrame | ChatTypingFrame;

/** Narrow a socket payload to a body-less chat frame. Unknown kinds are ignored. */
export function parseChatFrame(raw: unknown): ParsedChatFrame | null {
  if (typeof raw !== "object" || raw === null) return null;
  const frame = raw as Record<string, unknown>;
  if (frame.type !== "chat") return null;
  if (typeof frame.channel_id !== "string" || frame.channel_id === "") return null;
  if (frame.kind === "thread.updated") return parseThreadFrame(frame, frame.channel_id);
  if (frame.kind === "typing") return parseTypingFrame(frame, frame.channel_id);
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
 * applies at once and never waits for the backfill. A `typing` frame is
 * ephemeral and applies at once too.
 */
export function acceptChatFrame(frame: ParsedChatFrame): void {
  if (frame.kind === "typing") {
    applyTypingFrame(frame);
    return;
  }
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
 * True when this new change can move a listed sidebar count. My own messages
 * do not, and neither do thread replies or thread read cursors: counts only
 * cover top-level messages after the channel cursor. A channel bootstrap does
 * not list yet is left to the new-channel refetch in `ChatPage`, which brings
 * its counts along.
 */
export function changeMovesBadges(
  change: TeamChatChange,
  me: string | undefined,
  listed: ReadonlySet<string>,
): boolean {
  if (!listed.has(change.channel_id)) return false;
  if (change.kind === "read") return change.thread_root_id === null || change.thread_root_id === "";
  if (change.thread_root_id !== null) return false;
  return change.author_id === undefined || me === undefined || change.author_id !== me;
}

/** True when *body* carries a `<@user:me>` or `<@agent_account:me>` token (FR-008). */
export function mentionsPrincipal(body: string, me: string): boolean {
  if (me === "") return false;
  return body.includes(`<@user:${me}>`) || body.includes(`<@agent_account:${me}>`);
}

/** Epoch microseconds of a bootstrap `changes_cursor` (`<us>:<hex>`), or null. */
export function bootstrapCursorTime(cursor: string | undefined): number | null {
  if (cursor === undefined) return null;
  const value = Number(cursor.split(":", 1)[0]);
  return Number.isFinite(value) && value > 0 ? value : null;
}

/**
 * Unread and mention counts of one page after a channel read cursor, or null
 * when the page cannot tell: it has older messages and none of its rows is
 * at or before the cursor.
 */
export function countsAfterCursor(
  items: readonly ChatMessage[],
  cursor: ReadCursor,
  me: string,
  hasOlder: boolean,
): { unread: number; mentions: number } | null {
  let unread = 0;
  let mentions = 0;
  let reachesCursor = false;
  for (const item of items) {
    if (item.thread_root_id !== null) continue;
    if (!isCursorAfter({ id: item.id, created_at: item.created_at }, cursor)) {
      reachesCursor = true;
      continue;
    }
    if (item.author_id === me) continue;
    unread += 1;
    if (mentionsPrincipal(item.body, me)) mentions += 1;
  }
  if (hasOlder && !reachesCursor) return null;
  return { unread, mentions };
}

/** Bootstrap with one channel's counts replaced by `update`. Same object when nothing moved. */
export function withChannelCounts(
  data: BootstrapResponse,
  channelId: string,
  update: (unread: number, mentions: number) => { unread: number; mentions: number },
): BootstrapResponse {
  let moved = false;
  const apply = (rows: BootstrapResponse["channels"]) =>
    rows.map((row) => {
      if (row.id !== channelId) return row;
      const next = update(row.unread_count, row.mention_count);
      if (next.unread === row.unread_count && next.mentions === row.mention_count) return row;
      moved = true;
      return { ...row, unread_count: next.unread, mention_count: next.mentions };
    });
  const channels = apply(data.channels);
  const dms = apply(data.dms);
  return moved ? { ...data, channels, dms } : data;
}

/**
 * Keep listed sidebar counts current without a bootstrap GET (FR-008,
 * FR-009, AC-009a).
 *
 * A new top-level message in a listed channel waits for its row in that
 * channel's message page (`cacheChannelMessages` fills it). When the row is
 * from someone else, newer than the bootstrap counts and after the known
 * read cursor, unread goes up by one, and mentions too when the body names
 * me. A channel `read.updated` change recounts that channel from its page,
 * fetching the page when nothing has cached it. Each message counts once.
 */
function useBadgeSync(enabled: boolean): void {
  const queryClient = useQueryClient();
  useEffect(() => {
    if (!enabled) return;
    /** Message id -> channel id, for rows whose author and body are not loaded yet. */
    const pendingMessages = new Map<string, string>();
    /** Channel id -> newest channel read cursor not applied yet. */
    const pendingReads = new Map<string, ReadCursor>();
    /** Channel id -> newest channel read cursor seen while mounted. */
    const lastRead = new Map<string, ReadCursor>();
    const counted = new Set<string>();

    const resolve = (channelId: string): void => {
      const data = queryClient.getQueryData<BootstrapResponse>(teamChatQueryKeys.bootstrap);
      const page = queryClient.getQueryData<MessagePageResponse>(
        teamChatQueryKeys.messages(channelId),
      );
      if (data === undefined || page === undefined) return;
      const countedUntil = bootstrapCursorTime(data.changes_cursor);
      const read = lastRead.get(channelId) ?? null;
      const byId = new Map(page.items.map((item) => [item.id, item]));
      let next = data;
      for (const [messageId, pendingChannel] of pendingMessages) {
        if (pendingChannel !== channelId) continue;
        const item = byId.get(messageId);
        if (item === undefined) continue;
        pendingMessages.delete(messageId);
        if (counted.has(messageId)) continue;
        counted.add(messageId);
        if (item.thread_root_id !== null || item.author_id === data.me) continue;
        // Bootstrap already counted everything up to its cursor.
        if (countedUntil !== null && item.created_at <= countedUntil) continue;
        if (!isCursorAfter({ id: item.id, created_at: item.created_at }, read)) continue;
        const mention = mentionsPrincipal(item.body, data.me);
        next = withChannelCounts(next, channelId, (unread, mentions) => ({
          unread: unread + 1,
          mentions: mentions + (mention ? 1 : 0),
        }));
      }
      const cursor = pendingReads.get(channelId);
      if (cursor !== undefined) {
        pendingReads.delete(channelId);
        const counts = countsAfterCursor(page.items, cursor, data.me, page.has_older);
        if (counts !== null) next = withChannelCounts(next, channelId, () => counts);
      }
      if (next !== data) queryClient.setQueryData(teamChatQueryKeys.bootstrap, next);
    };

    /** Resolve from the cached page, or fetch that page (never bootstrap) for a read. */
    const resolveOrLoad = (channelId: string, fetchPage: boolean): void => {
      const queryKey = teamChatQueryKeys.messages(channelId);
      if (queryClient.getQueryData(queryKey) !== undefined || !fetchPage) {
        resolve(channelId);
        return;
      }
      // Joins a fill already in flight; the cache event below resolves it.
      void queryClient
        .fetchQuery({
          queryKey,
          queryFn: ({ signal }) => fetchChannelMessages(channelId, signal),
          staleTime: 0,
        })
        .catch((error: unknown) => {
          const status = error instanceof ChatApiError ? String(error.status) : "error";
          console.warn(`[team-chat] badge page fetch failed (${status})`);
        });
    };

    let known = new Set(Object.keys(useTeamChatStore.getState().changes));
    const unsubscribeStore = useTeamChatStore.subscribe((state, previous) => {
      if (state.changes === previous.changes) return;
      const data = queryClient.getQueryData<BootstrapResponse>(teamChatQueryKeys.bootstrap);
      const listed = new Set([...(data?.channels ?? []), ...(data?.dms ?? [])].map((c) => c.id));
      const messageChannels = new Set<string>();
      const readChannels = new Set<string>();
      for (const change of Object.values(state.changes)) {
        const key = teamChatChangeKey(change);
        if (known.has(key)) continue;
        if (!changeMovesBadges(change, data?.me, listed)) continue;
        if (change.kind === "read") {
          const cursor = { id: change.id, created_at: change.created_at };
          if (!isCursorAfter(cursor, lastRead.get(change.channel_id) ?? null)) continue;
          lastRead.set(change.channel_id, cursor);
          pendingReads.set(change.channel_id, cursor);
          readChannels.add(change.channel_id);
        } else if (!counted.has(change.id)) {
          pendingMessages.set(change.id, change.channel_id);
          messageChannels.add(change.channel_id);
        }
      }
      known = new Set(Object.keys(state.changes));
      // A message row may already be cached; otherwise its fill resolves it.
      for (const channelId of messageChannels) {
        if (!readChannels.has(channelId)) resolveOrLoad(channelId, false);
      }
      for (const channelId of readChannels) resolveOrLoad(channelId, true);
    });
    const unsubscribeCache = queryClient.getQueryCache().subscribe((event) => {
      if (event.type !== "updated") return;
      const [scope, kind, channelId] = event.query.queryKey as readonly unknown[];
      if (scope !== "team-chat" || kind !== "messages" || typeof channelId !== "string") return;
      if (event.query.state.data !== undefined) resolve(channelId);
    });
    return () => {
      unsubscribeStore();
      unsubscribeCache();
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
  useBadgeSync(enabled);
  useEffect(() => {
    if (!enabled || pendingChannels.length === 0) return;
    const channelIds = useTeamChatStore.getState().takePendingChannels();
    for (const channelId of channelIds) cacheChannelMessages(queryClient, channelId);
  }, [enabled, pendingChannels, queryClient]);
}
