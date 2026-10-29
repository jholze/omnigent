/**
 * Open channel or DM: newest 50 top-level messages plus the composer.
 *
 * The list renders from the `teamChatQueryKeys.messages` cache. A
 * `message.created` frame carries no body; `useChatFrames` refetches that
 * cache and the list follows (AC-013a). Optimistic sends come from the outbox
 * until the stored row with the same `client_id` is in the cache.
 *
 * The pending-request strip is not limited to that page. Active agent
 * threads come from `GET .../agent-threads` (AC-107b). The chip still
 * decides which of those rows stay visible.
 */
import { useEffect, useMemo, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  fetchChannelAgentThreads,
  fetchChannelMessages,
  teamChatQueryKeys,
  type ChannelAgentThread,
  type ChatMessage,
  type DirectoryEntry,
} from "./chatApi";
import { AgentRootChip } from "./agent/AgentStatusChip";
import { PendingAgentRequests } from "./agent/PendingAgentRequests";
import {
  useAgentThreadStore,
  useSeedAgentThreads,
  useSeedChannelAgentThreads,
  useWatchAgentSessions,
} from "./agent/agentThreadStore";
import { ChatComposerCard } from "./ChatComposerCard";
import { fetchLatest } from "./freshQuery";
import { messageQueryOptions } from "./messagePage";
import { retryChatMessage, useChatOutbox, type OutboxEntry } from "./chatOutbox";
import { toEpochMs, type MessageView } from "./MessageItem";
import { MessageList } from "./MessageList";
import { CHAT_TEST_IDS } from "./testids";
import { useTeamChatStore } from "./teamChatStore";
import { TypingIndicator } from "./TypingIndicator";
import { newestTopLevel, useReadCursor } from "./useReadCursor";

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
    ...(message.forward_state ? { forwardState: message.forward_state } : {}),
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
    rateLimited: entry.status === "failed" ? (entry.rateLimited ?? null) : null,
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

/** One active agent thread the strip may name. `body` null means the root text is not loaded. */
export interface ListedAgentThread {
  rootId: string;
  body: string | null;
}

/**
 * Strip candidates for one channel (AC-107b).
 *
 * Every listed active agent thread is included, even when its root is
 * outside the loaded tail. Tail roots the store already treats as agent
 * threads stay candidates too, so a row still shows before the list
 * returns. An empty body is the "Agent-Thread" fallback.
 */
export function pendingRequestCandidates(
  channelId: string,
  listed: readonly ListedAgentThread[],
  tail: readonly ChatMessage[],
  agentRootIds: ReadonlySet<string>,
): { rootId: string; body: string; href: string }[] {
  const tailBody = new Map<string, string>();
  for (const message of tail) {
    if (message.thread_root_id !== null) continue;
    tailBody.set(message.id, message.body);
  }
  const seen = new Set<string>();
  const roots: { rootId: string; body: string; href: string }[] = [];
  const push = (rootId: string, body: string) => {
    if (rootId === "" || seen.has(rootId)) return;
    seen.add(rootId);
    roots.push({ rootId, body, href: threadPath(channelId, rootId) });
  };
  for (const thread of listed) {
    const fromTail = tailBody.get(thread.rootId);
    const body = fromTail !== undefined && fromTail !== "" ? fromTail : (thread.body ?? "");
    push(thread.rootId, body);
  }
  for (const message of tail) {
    if (message.thread_root_id !== null || !agentRootIds.has(message.id)) continue;
    push(message.id, message.body);
  }
  for (const rootId of [...agentRootIds].filter((id) => !seen.has(id)).sort()) {
    push(rootId, tailBody.get(rootId) ?? "");
  }
  return roots;
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

/**
 * Watch Agent-Thread sessions for this channel (FR-047, AC-107b).
 *
 * The tail page is not enough: active threads from the channel list, and
 * any active thread a `thread.updated` frame added, are watched too. The
 * returned set is every such root, so the strip can name ones the page
 * did not load.
 */
function useChannelAgentThreads(
  channelId: string,
  tailRootIds: string[],
  listed: readonly ChannelAgentThread[],
): Set<string> {
  const tailKey = tailRootIds.join(",");
  const listedKey = listed
    .map((thread) => `${thread.root_message_id}=${thread.session_id ?? ""}`)
    .sort()
    .join(",");
  const threadKey = useAgentThreadStore((state) => {
    const tails = new Set(tailKey === "" ? [] : tailKey.split(","));
    const listedRoots = new Set(
      listedKey === "" ? [] : listedKey.split(",").map((pair) => pair.split("=")[0] ?? ""),
    );
    return Object.values(state.threads)
      .filter((thread) => {
        if (thread.channelId !== channelId) return false;
        if (tails.has(thread.rootId) || listedRoots.has(thread.rootId)) return true;
        return thread.agentState === "active" || thread.agentState === "starting";
      })
      .map((thread) => `${thread.rootId}=${thread.sessionId ?? ""}`)
      .sort()
      .join(",");
  });
  const { agentRoots, sessionIds } = useMemo(() => {
    const fromStore = threadKey === "" ? [] : threadKey.split(",").map((pair) => pair.split("="));
    const fromListed = listedKey === "" ? [] : listedKey.split(",").map((pair) => pair.split("="));
    const sessions = new Set<string>();
    for (const [, session] of [...fromStore, ...fromListed]) {
      if (session) sessions.add(session);
    }
    return {
      agentRoots: new Set(fromStore.map(([root]) => root).filter((root) => root !== "")),
      sessionIds: [...sessions].sort(),
    };
  }, [threadKey, listedKey]);
  useWatchAgentSessions(`channel:${channelId}`, sessionIds);
  return agentRoots;
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
  const messages = useQuery(messageQueryOptions(channelId));
  const refillAttempts = useRef(new Map<string, number>());
  const pendingRootIds = useTeamChatStore((state) =>
    Object.values(state.changes)
      .filter(
        (change) =>
          change.kind === "message" &&
          change.channel_id === channelId &&
          change.thread_root_id === null,
      )
      .map((change) => change.id)
      .sort()
      .join(","),
  );
  // A page fetched before the message committed can stay empty. When the
  // change feed already names a root that the page lacks, load the page again.
  useEffect(() => {
    if (pendingRootIds === "" || messages.data === undefined) return;
    const loaded = new Set(messages.data.items.map((item) => item.id));
    const missing = pendingRootIds.split(",").filter((id) => id !== "" && !loaded.has(id));
    if (missing.length === 0) return;
    const key = missing.join(",");
    const attempts = refillAttempts.current.get(key) ?? 0;
    if (attempts >= 3) return;
    refillAttempts.current.set(key, attempts + 1);
    void fetchLatest(queryClient, teamChatQueryKeys.messages(channelId), (signal) =>
      fetchChannelMessages(channelId, signal),
    );
  }, [pendingRootIds, messages.data, channelId, queryClient]);
  const agentThreads = useQuery({
    queryKey: teamChatQueryKeys.agentThreads(channelId),
    queryFn: ({ signal }) => fetchChannelAgentThreads(channelId, signal),
    // A thread can start, or an approval can open, while the channel is closed.
    refetchOnMount: "always",
  });
  const outbox = useChatOutbox((state) => state.entries);
  const replyCounts = useReplyCounts(channelId);
  // The newest stored message on screen moves the read cursor (FR-007).
  const viewRef = useRef<HTMLDivElement>(null);
  const newestStored = useMemo(() => newestTopLevel(messages.data?.items), [messages.data]);
  useReadCursor(channelId, newestStored, viewRef);
  const views = useMemo(
    () => withOutbox(messages.data?.items ?? [], outbox, channelId, null),
    [messages.data, outbox, channelId],
  );
  const rootIds = useMemo(
    () => (messages.data?.items ?? []).map((message) => message.id),
    [messages.data],
  );
  useSeedAgentThreads(messages.data?.items);
  useSeedChannelAgentThreads(channelId, agentThreads.data);
  const agentRoots = useChannelAgentThreads(channelId, rootIds, agentThreads.data ?? []);
  const requestRoots = useMemo(
    () =>
      pendingRequestCandidates(
        channelId,
        (agentThreads.data ?? []).map((thread) => ({
          rootId: thread.root_message_id,
          body: thread.body ?? null,
        })),
        messages.data?.items ?? [],
        agentRoots,
      ),
    [messages.data, agentThreads.data, agentRoots, channelId],
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
        renderThreadAccessory={(message) =>
          message.id && agentRoots.has(message.id) ? <AgentRootChip rootId={message.id} /> : null
        }
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
    <div ref={viewRef} className="flex min-h-0 min-w-0 flex-1 flex-col">
      <PendingAgentRequests roots={requestRoots} directory={directory} />
      {body}
      <TypingIndicator channelId={channelId} me={me} directory={directory} />
      <ChatComposerCard
        channelId={channelId}
        me={me}
        placeholder={`Nachricht an ${label}`}
        label={`Nachricht an ${label}`}
        directory={directory}
      />
    </div>
  );
}
