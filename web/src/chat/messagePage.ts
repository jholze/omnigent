/**
 * Keep a message a stale page omitted.
 *
 * A tail fetched before the send commits can land afterwards and replace the
 * cache with an empty page (AC-021a). `setQueryData` and a finished fetch both
 * go through this share function, so the write sees the row that is already
 * cached. A tail that starts after a row still drops that row: it scrolled out
 * of the page. This build has no delete, so a row inside the incoming window
 * stays.
 */
import type { QueryFunction } from "@tanstack/react-query";

import {
  fetchChannelMessages,
  teamChatQueryKeys,
  type ChatMessage,
  type MessagePageResponse,
} from "./chatApi";

function isPage(value: unknown): value is MessagePageResponse {
  if (typeof value !== "object" || value === null) return false;
  return Array.isArray((value as { items?: unknown }).items);
}

export function mergeMessagePage(
  previous: MessagePageResponse | undefined,
  incoming: MessagePageResponse,
): MessagePageResponse {
  if (previous === undefined) return incoming;
  const incomingIds = new Set(incoming.items.map((item) => item.id));
  const oldestIncoming =
    incoming.items.length === 0
      ? null
      : incoming.items.reduce(
          (oldest, item) => Math.min(oldest, item.created_at),
          Number.POSITIVE_INFINITY,
        );
  const kept = previous.items.filter((item) => {
    if (incomingIds.has(item.id)) return false;
    if (oldestIncoming === null) return true;
    return item.created_at >= oldestIncoming;
  });
  if (kept.length === 0) return incoming;
  const items = [...incoming.items, ...kept].sort(byTimeThenId);
  return { ...incoming, items };
}

function byTimeThenId(a: ChatMessage, b: ChatMessage): number {
  return a.created_at - b.created_at || a.id.localeCompare(b.id);
}

/** `structuralSharing` for a message page. Non-pages pass through. */
export function shareMessagePage(previous: unknown, incoming: unknown): unknown {
  if (!isPage(incoming) || !isPage(previous)) return incoming;
  return mergeMessagePage(previous, incoming);
}

/** Channel message tail. Both open views share this so neither drops the merge. */
export function messageQueryOptions(channelId: string): {
  queryKey: ReturnType<typeof teamChatQueryKeys.messages>;
  queryFn: QueryFunction<MessagePageResponse>;
  refetchOnMount: "always";
  structuralSharing: typeof shareMessagePage;
} {
  return {
    queryKey: teamChatQueryKeys.messages(channelId),
    queryFn: ({ signal }) => fetchChannelMessages(channelId, signal),
    refetchOnMount: "always",
    structuralSharing: shareMessagePage,
  };
}
