/**
 * Open channel or DM: newest 50 top-level messages plus the composer.
 *
 * The list renders from the `teamChatQueryKeys.messages` cache. A
 * `message.created` frame carries no body; `useChatFrames` refetches that
 * cache and the list follows (AC-013a). Optimistic sends come from the outbox
 * until the stored row with the same `client_id` is in the cache.
 */
import { useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  fetchChannelMessages,
  teamChatQueryKeys,
  type ChatMessage,
  type DirectoryEntry,
} from "./chatApi";
import { ChatComposerCard } from "./ChatComposerCard";
import { retryChatMessage, useChatOutbox, type OutboxEntry } from "./chatOutbox";
import { toEpochMs, type MessageView } from "./MessageItem";
import { MessageList } from "./MessageList";
import { CHAT_TEST_IDS } from "./testids";
import { useTeamChatStore } from "./teamChatStore";

export function messageViewOf(message: ChatMessage): MessageView {
  return {
    key: message.id,
    id: message.id,
    clientId: message.client_id,
    channelId: message.channel_id,
    threadRootId: message.thread_root_id,
    authorId: message.author_id,
    authorKind: message.author_kind,
    createdAtMs: toEpochMs(message.created_at),
    body: message.body,
    status: "sent",
  };
}

function outboxViewOf(entry: OutboxEntry): MessageView {
  return {
    key: `client:${entry.clientId}`,
    id: null,
    clientId: entry.clientId,
    channelId: entry.channelId,
    threadRootId: entry.threadRootId,
    authorId: entry.authorId,
    authorKind: "user",
    createdAtMs: entry.createdAtMs,
    body: entry.body,
    status: entry.status,
  };
}

/** Stored rows, then pending sends whose `client_id` the server has not returned yet. */
export function withOutbox(
  stored: ChatMessage[],
  outbox: OutboxEntry[],
  channelId: string,
  threadRootId: string | null,
): MessageView[] {
  const storedClientIds = new Set(
    stored.flatMap((message) => (message.client_id ? [message.client_id] : [])),
  );
  const pending = outbox.filter(
    (entry) =>
      entry.channelId === channelId &&
      entry.threadRootId === threadRootId &&
      !storedClientIds.has(entry.clientId),
  );
  return [...stored.map(messageViewOf), ...pending.map(outboxViewOf)];
}

export function directoryMap(directory: DirectoryEntry[]): Map<string, DirectoryEntry> {
  return new Map(directory.map((entry) => [entry.id, entry]));
}

export function threadPath(channelId: string, threadRootId: string): string {
  return `/chat/${encodeURIComponent(channelId)}/t/${encodeURIComponent(threadRootId)}`;
}

/**
 * Reply ids per thread root in one channel, from frames, backfill, own sends
 * and loaded thread pages (all merged by id into the change set). The message
 * wire has no `reply_count`, so a reload shows counts only for threads seen
 * in this session.
 */
export function useReplyCounts(channelId: string): Map<string, number> {
  const changes = useTeamChatStore((state) => state.changes);
  return useMemo(() => {
    const byRoot = new Map<string, Set<string>>();
    for (const change of Object.values(changes)) {
      if (change.kind !== "message" || change.channel_id !== channelId) continue;
      if (change.thread_root_id === null) continue;
      const ids = byRoot.get(change.thread_root_id) ?? new Set<string>();
      ids.add(change.id);
      byRoot.set(change.thread_root_id, ids);
    }
    return new Map([...byRoot].map(([root, ids]) => [root, ids.size]));
  }, [changes, channelId]);
}

export function MessagesSkeleton() {
  return (
    <div aria-busy="true" className="flex min-h-0 flex-1 flex-col justify-end gap-4 px-4 pb-4">
      <span className="sr-only">Nachrichten werden geladen…</span>
      {["w-2/5", "w-2/3", "w-1/4"].map((width) => (
        <div key={width} aria-hidden="true" className="flex gap-3">
          <div className="size-7 shrink-0 rounded-md bg-muted" />
          <div className="flex min-w-0 flex-1 flex-col gap-2 pt-1">
            <div className="h-3 w-24 rounded-sm bg-muted" />
            <div className={cn("h-3 rounded-sm bg-muted", width)} />
          </div>
        </div>
      ))}
    </div>
  );
}

export function MessagesError({ onRetry, busy }: { onRetry: () => void; busy: boolean }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
      <p
        role="alert"
        data-testid={CHAT_TEST_IDS.messagesError}
        className="text-sm text-muted-foreground"
      >
        Nachrichten konnten nicht geladen werden
      </p>
      <Button
        variant="outline"
        componentId="chat.messages.retry"
        data-testid={CHAT_TEST_IDS.messagesRetry}
        className="min-h-11 px-4"
        loading={busy}
        onClick={onRetry}
      >
        Erneut versuchen
      </Button>
    </div>
  );
}

export interface ChannelViewProps {
  channelId: string;
  /** "#general" or the DM label, for the list name and placeholder. */
  label: string;
  me: string;
  directory: Map<string, DirectoryEntry>;
}

export function ChannelView({ channelId, label, me, directory }: ChannelViewProps) {
  const queryClient = useQueryClient();
  const messages = useQuery({
    queryKey: teamChatQueryKeys.messages(channelId),
    queryFn: ({ signal }) => fetchChannelMessages(channelId, signal),
  });
  const outbox = useChatOutbox((state) => state.entries);
  const replyCounts = useReplyCounts(channelId);
  const views = useMemo(
    () => withOutbox(messages.data?.items ?? [], outbox, channelId, null),
    [messages.data, outbox, channelId],
  );

  let body;
  if (messages.isPending) {
    body = <MessagesSkeleton />;
  } else if (messages.isError && messages.data === undefined) {
    body = <MessagesError busy={messages.isFetching} onRetry={() => void messages.refetch()} />;
  } else {
    body = (
      <MessageList
        key={channelId}
        messages={views}
        directory={directory}
        me={me}
        label={`Nachrichten in ${label}`}
        threadHrefFor={(message) =>
          message.id && message.threadRootId === null
            ? threadPath(channelId, message.id)
            : undefined
        }
        replyCounts={replyCounts}
        onRetry={(clientId) => retryChatMessage(queryClient, clientId)}
        empty={
          <div
            data-testid={CHAT_TEST_IDS.messagesEmpty}
            className="flex h-full flex-col items-center justify-center gap-1 p-6 text-center"
          >
            <p className="text-sm font-medium">Noch keine Nachrichten</p>
            <p className="text-sm text-muted-foreground">Schreib unten die erste.</p>
          </div>
        }
      />
    );
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      {body}
      <ChatComposerCard
        channelId={channelId}
        me={me}
        placeholder={`Nachricht an ${label}`}
        label={`Nachricht an ${label}`}
      />
    </div>
  );
}
