/**
 * Thread pane (`/chat/:channelId/t/:threadRootId`): root message, replies and
 * a reply composer. Slack-like layout, built from our own pieces.
 *
 * Desktop: a column right of the channel. Phone: replaces the channel column
 * and brings its own back control. Escape closes the pane.
 *
 * An Agent-Thread adds the status chip to the header and the live agent
 * output below the stored replies (T-008).
 */
import { useEffect, useMemo, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeftIcon, XIcon } from "lucide-react";

import { Link, useNavigate } from "@/lib/routing";
import { cn } from "@/lib/utils";
import {
  fetchChannelMessages,
  fetchThreadMessages,
  teamChatQueryKeys,
  type DirectoryEntry,
} from "./chatApi";
import { AgentThreadStatus } from "./agent/AgentStatusChip";
import { AgentThreadView } from "./agent/AgentThreadView";
import {
  useAgentThreadStore,
  useSeedAgentThreads,
  useWatchAgentSessions,
} from "./agent/agentThreadStore";
import { ChatComposerCard } from "./ChatComposerCard";
import { MessagesError, MessagesSkeleton, messageViewOf, withOutbox } from "./ChannelView";
import { retryChatMessage, useChatOutbox } from "./chatOutbox";
import {
  isAgentKind,
  MessageItem,
  principalLabel,
  replyCountLabel,
  type MessageView,
} from "./MessageItem";
import { MessageList } from "./MessageList";
import { CHAT_TEST_IDS } from "./testids";
import { useTeamChatStore } from "./teamChatStore";

export interface ThreadPaneProps {
  channelId: string;
  threadRootId: string;
  channelLabel: string;
  me: string;
  directory: Map<string, DirectoryEntry>;
  className?: string;
}

const TITLE_ID = "chat-thread-title";

const AGENT_MENTION_RE = /<@omnigent_agent:[^>\s]+>/;
/** Prefix of the server's sharing notice (`chat_agent_threads._post_sharing_notice`). */
const SHARING_NOTICE_PREFIX = "Live-Ansicht nur für ";

/**
 * The person who started the agent: the first human message in the thread
 * that mentions an Omnigent agent. The server starts the session as that
 * author. A bot mention starts it as the bot's owner, which this view does
 * not know, so it returns `null` there.
 */
function agentStarterLabel(
  messages: MessageView[],
  directory: Map<string, DirectoryEntry>,
): string | null {
  const starter = messages.find((message) => AGENT_MENTION_RE.test(message.body));
  if (starter === undefined || starter.authorKind !== "user") return null;
  return principalLabel(starter.authorId, directory);
}

export function ThreadPane({
  channelId,
  threadRootId,
  channelLabel,
  me,
  directory,
  className,
}: ThreadPaneProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const channelPath = `/chat/${encodeURIComponent(channelId)}`;
  const threadKey = useMemo(
    () => teamChatQueryKeys.thread(channelId, threadRootId),
    [channelId, threadRootId],
  );
  const channelMessages = useQuery({
    queryKey: teamChatQueryKeys.messages(channelId),
    queryFn: ({ signal }) => fetchChannelMessages(channelId, signal),
  });
  const replies = useQuery({
    queryKey: threadKey,
    queryFn: ({ signal }) => fetchThreadMessages(channelId, threadRootId, signal),
  });
  const outbox = useChatOutbox((state) => state.entries);
  const root = channelMessages.data?.items.find((message) => message.id === threadRootId);
  useSeedAgentThreads(channelMessages.data?.items);

  // Loaded replies count toward "n Antworten" on the root row.
  useEffect(() => {
    const items = replies.data?.items;
    if (!items || items.length === 0) return;
    useTeamChatStore.getState().mergeChanges(
      items.map((message) => ({
        id: message.id,
        kind: "message" as const,
        channel_id: message.channel_id,
        thread_root_id: message.thread_root_id,
        created_at: message.created_at,
        author_id: message.author_id,
      })),
    );
  }, [replies.data]);

  // A frame names a reply this page lacks: refetch once per id.
  const liveIds = useTeamChatStore((state) =>
    Object.values(state.changes)
      .filter(
        (change) =>
          change.kind === "message" &&
          change.channel_id === channelId &&
          change.thread_root_id === threadRootId,
      )
      .map((change) => change.id)
      .sort()
      .join(","),
  );
  const requestedRef = useRef(new Set<string>());
  useEffect(() => {
    if (replies.data === undefined || liveIds === "") return;
    const loaded = new Set(replies.data.items.map((message) => message.id));
    const missing = liveIds
      .split(",")
      .filter((id) => !loaded.has(id) && !requestedRef.current.has(id));
    if (missing.length === 0) return;
    for (const id of missing) requestedRef.current.add(id);
    void queryClient.invalidateQueries({ queryKey: threadKey });
  }, [liveIds, replies.data, queryClient, threadKey]);

  const views = useMemo(
    () => withOutbox(replies.data?.items ?? [], outbox, channelId, threadRootId),
    [replies.data, outbox, channelId, threadRootId],
  );
  const sentCount = views.filter((view) => view.status === "sent").length;
  const onRetry = (clientId: string) => retryChatMessage(queryClient, clientId);

  const agentSessionId = useAgentThreadStore(
    (state) => state.threads[threadRootId]?.sessionId ?? null,
  );
  useWatchAgentSessions(`thread:${threadRootId}`, agentSessionId ? [agentSessionId] : []);
  const agentReplyCount = views.filter(
    (view) =>
      view.status === "sent" && (view.authorKind === "system" || isAgentKind(view.authorKind)),
  ).length;
  const sharingNoticeStored = views.some(
    (view) => view.authorKind === "system" && view.body.startsWith(SHARING_NOTICE_PREFIX),
  );
  const ownerLabel = useMemo(
    () => agentStarterLabel([...(root ? [messageViewOf(root)] : []), ...views], directory),
    [root, views, directory],
  );
  const agentFooter = agentSessionId ? (
    <AgentThreadView
      key={agentSessionId}
      sessionId={agentSessionId}
      me={me}
      directory={directory}
      ownerLabel={ownerLabel}
      agentReplyCount={agentReplyCount}
      sharingNoticeStored={sharingNoticeStored}
    />
  ) : null;

  let rootBlock;
  if (root) {
    rootBlock = (
      <ul aria-label="Ursprüngliche Nachricht" className="pt-2 pb-1">
        <MessageItem message={messageViewOf(root)} directory={directory} />
      </ul>
    );
  } else if (channelMessages.isPending) {
    rootBlock = <div aria-hidden="true" className="mx-4 mt-3 h-10 rounded-md bg-muted" />;
  } else {
    rootBlock = (
      <p className="px-4 pt-3 pb-1 text-sm text-muted-foreground">
        Die ursprüngliche Nachricht ist hier nicht geladen.
      </p>
    );
  }

  const header = (
    <>
      {rootBlock}
      <div className="flex items-center gap-3 px-4 py-2">
        <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
          {sentCount === 0 ? "Noch keine Antworten" : replyCountLabel(sentCount)}
        </span>
        <span aria-hidden="true" className="h-px flex-1 bg-border" />
      </div>
    </>
  );

  let body;
  if (replies.isPending) {
    body = (
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        {header}
        <MessagesSkeleton />
        {agentFooter}
      </div>
    );
  } else if (replies.isError && replies.data === undefined) {
    body = <MessagesError busy={replies.isFetching} onRetry={() => void replies.refetch()} />;
  } else {
    body = (
      <MessageList
        key={`${channelId}:${threadRootId}`}
        messages={views}
        directory={directory}
        me={me}
        label="Antworten im Thread"
        header={header}
        footer={agentFooter}
        onRetry={onRetry}
      />
    );
  }

  return (
    <section
      data-testid={CHAT_TEST_IDS.threadPane}
      aria-labelledby={TITLE_ID}
      className={cn(
        "flex min-h-0 min-w-0 flex-col max-md:w-full max-md:flex-1 md:w-80 md:shrink-0 md:border-l md:border-border lg:w-96",
        className,
      )}
      onKeyDown={(event) => {
        if (event.key !== "Escape" || event.defaultPrevented || event.nativeEvent.isComposing) {
          return;
        }
        event.preventDefault();
        navigate(channelPath);
      }}
    >
      <Link
        to={channelPath}
        componentId="chat.thread.back"
        data-testid={CHAT_TEST_IDS.threadBack}
        className="flex min-h-11 min-w-0 items-center gap-1 border-b border-border px-3 text-sm text-muted-foreground hover:text-foreground md:hidden"
      >
        <ChevronLeftIcon className="size-4 shrink-0" aria-hidden="true" />
        <span className="min-w-0 break-words">{channelLabel}</span>
      </Link>
      <header className="flex min-h-11 items-center gap-2 border-b border-border py-1 pr-2 pl-4">
        <h2 id={TITLE_ID} className="shrink-0 text-sm font-medium">
          Thread
        </h2>
        <AgentThreadStatus rootId={threadRootId} />
        <span className="min-w-0 flex-1 text-xs break-words text-muted-foreground max-md:hidden">
          {channelLabel}
        </span>
        <Link
          to={channelPath}
          componentId="chat.thread.close"
          aria-label="Thread schließen"
          title="Thread schließen (Esc)"
          data-testid={CHAT_TEST_IDS.threadClose}
          className="ml-auto flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring max-md:hidden"
        >
          <XIcon className="size-4" aria-hidden="true" />
        </Link>
      </header>
      {body}
      <ChatComposerCard
        channelId={channelId}
        threadRootId={threadRootId}
        me={me}
        placeholder="Im Thread antworten…"
        label={`Antwort im Thread in ${channelLabel}`}
        directory={directory}
      />
    </section>
  );
}
