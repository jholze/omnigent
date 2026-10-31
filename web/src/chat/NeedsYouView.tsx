/**
 * "Braucht dich" (FR-010): unread mentions of the caller and agent approvals
 * the caller may answer (level >= EDIT, filtered on the server), each linking
 * to its thread. Rendered at the top of the inbox.
 *
 * `useNeedsYouNotifications` turns new rows and new DM messages into OS
 * notifications with a generic body (FR-011). It never asks for permission;
 * with `denied` only in-app badges change (FR-012).
 *
 * No message text reaches this module: `GET /v1/chat/needs-you` returns
 * places and names only.
 */
import { useEffect, useRef } from "react";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { AtSignIcon, MessageCircleQuestionMarkIcon, type LucideIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { authenticatedFetch } from "@/lib/identity";
import { showChatNotification, type ChatNotificationEvent } from "@/lib/browserNotifications";
import { Link, useLocation, useNavigate } from "@/lib/routing";
import { cn } from "@/lib/utils";

import {
  ChatApiError,
  fetchBootstrap,
  teamChatQueryKeys,
  type BootstrapResponse,
  type DirectoryEntry,
} from "./chatApi";
import { formatMessageTime, toEpochMs } from "./MessageItem";
import { useTeamChatStore, type TeamChatChange } from "./teamChatStore";

/** One unread message that mentions the caller. No body. */
export interface NeedsYouMention {
  channel_id: string;
  channel_kind: string;
  channel_name: string | null;
  thread_root_id: string | null;
  message_id: string;
  author_id: string;
  author_label: string;
  /** Epoch microseconds. */
  created_at: number;
}

/** One agent thread whose session waits on an answer the caller may give. */
export interface NeedsYouApproval {
  channel_id: string;
  channel_kind: string;
  channel_name: string | null;
  root_message_id: string;
  session_id: string;
  pending_count: number;
}

/** `GET /v1/chat/needs-you`. */
export interface NeedsYouResponse {
  mentions: NeedsYouMention[];
  approvals: NeedsYouApproval[];
}

export const needsYouQueryKey = ["team-chat", "needs-you"] as const;

/** `data-testid` values of this view. */
export const NEEDS_YOU_TEST_IDS = {
  view: "needs-you-view",
  item: "needs-you-item",
  loading: "needs-you-loading",
  error: "needs-you-error",
  retry: "needs-you-retry",
} as const;

/** Safety net next to frame-driven refetches: approvals do not arrive as chat frames. */
const NEEDS_YOU_POLL_MS = 30_000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isNeedsYouResponse(value: unknown): value is NeedsYouResponse {
  return isRecord(value) && Array.isArray(value.mentions) && Array.isArray(value.approvals);
}

export async function fetchNeedsYou(signal?: AbortSignal): Promise<NeedsYouResponse> {
  const response = await authenticatedFetch("/v1/chat/needs-you", { signal });
  if (!response.ok) throw new ChatApiError(response.status);
  const payload: unknown = await response.json();
  if (!isNeedsYouResponse(payload)) throw new Error("Invalid needs-you response");
  return payload;
}

/** `#billing`, `Direktnachricht`, or the stored name of any other container. */
export function conversationLabel(kind: string, name: string | null): string {
  if (kind === "dm") return "Direktnachricht";
  const trimmed = name?.trim();
  if (!trimmed) return "Unterhaltung";
  return kind === "channel" ? `#${trimmed}` : trimmed;
}

function chatPath(channelId: string, threadRootId: string | null): string {
  const channel = `/chat/${encodeURIComponent(channelId)}`;
  return threadRootId ? `${channel}/t/${encodeURIComponent(threadRootId)}` : channel;
}

export function mentionPath(mention: NeedsYouMention): string {
  return chatPath(mention.channel_id, mention.thread_root_id);
}

export function approvalPath(approval: NeedsYouApproval): string {
  return chatPath(approval.channel_id, approval.root_message_id);
}

/**
 * Human label for a principal: directory name, else the email local part
 * with a capital first letter (`jens@…` → `Jens`), same rule as the server.
 */
export function chatPersonName(id: string, directory?: readonly DirectoryEntry[]): string {
  const entry = directory?.find((row) => row.id === id);
  const name = entry?.display_name?.trim() || entry?.handle?.trim();
  if (name) return name;
  const local = id.split("@", 1)[0]?.trim() || id;
  return local.charAt(0).toUpperCase() + local.slice(1);
}

/**
 * Refetch needs-you when a chat message frame lands. Frames carry no body,
 * so the server decides whether it was a mention.
 */
function useRefetchOnChatMessages(queryClient: QueryClient, enabled: boolean): void {
  useEffect(() => {
    if (!enabled) return;
    return useTeamChatStore.subscribe((state, prev) => {
      if (state.changes === prev.changes) return;
      for (const [key, change] of Object.entries(state.changes)) {
        if (change.kind === "message" && !Object.hasOwn(prev.changes, key)) {
          void queryClient.invalidateQueries({ queryKey: needsYouQueryKey });
          return;
        }
      }
    });
  }, [enabled, queryClient]);
}

export function useNeedsYou(enabled: boolean) {
  const queryClient = useQueryClient();
  useRefetchOnChatMessages(queryClient, enabled);
  return useQuery({
    queryKey: needsYouQueryKey,
    queryFn: ({ signal }) => fetchNeedsYou(signal),
    enabled,
    refetchInterval: NEEDS_YOU_POLL_MS,
    retry: 1,
  });
}

type NeedsYouRow =
  | { kind: "approval"; key: string; approval: NeedsYouApproval }
  | { kind: "mention"; key: string; mention: NeedsYouMention };

/** Approvals first (an agent is blocked), then mentions newest first. */
export function needsYouRows(data: NeedsYouResponse | undefined): NeedsYouRow[] {
  if (!data) return [];
  return [
    ...data.approvals.map((approval) => ({
      kind: "approval" as const,
      key: `approval:${approval.session_id}:${approval.root_message_id}`,
      approval,
    })),
    ...data.mentions.map((mention) => ({
      kind: "mention" as const,
      key: `mention:${mention.message_id}`,
      mention,
    })),
  ];
}

const countFormat = new Intl.NumberFormat("de-DE");

function openApprovalsLabel(n: number): string {
  return n === 1 ? "1 offene Freigabe" : `${countFormat.format(n)} offene Freigaben`;
}

/** The "Braucht dich" section. Renders nothing while `enabled` is false. */
export function NeedsYouView({ enabled }: { enabled: boolean }) {
  const query = useNeedsYou(enabled);
  if (!enabled) return null;
  const rows = needsYouRows(query.data);

  return (
    <section
      aria-labelledby="needs-you-heading"
      aria-busy={query.isLoading || undefined}
      data-testid={NEEDS_YOU_TEST_IDS.view}
      className="mb-6 flex flex-col gap-1"
    >
      <div className="flex items-baseline gap-2 px-3 py-1.5">
        <h2 id="needs-you-heading" className="text-sm font-medium text-balance">
          Braucht dich
        </h2>
        {rows.length > 0 && (
          <span className="text-sm text-muted-foreground tabular-nums">
            {countFormat.format(rows.length)}
          </span>
        )}
      </div>

      {query.isLoading && (
        <ul data-testid={NEEDS_YOU_TEST_IDS.loading} aria-label="Wird geladen…">
          {[0, 1].map((n) => (
            <li key={n} aria-hidden className="flex min-h-11 items-center gap-3 px-3 py-2">
              <span className="size-8 shrink-0 rounded-lg bg-muted" />
              <span className="h-3 w-48 max-w-full rounded-sm bg-muted" />
            </li>
          ))}
        </ul>
      )}

      {query.isError && (
        <div
          data-testid={NEEDS_YOU_TEST_IDS.error}
          className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm"
        >
          <span className="min-w-0 text-pretty text-destructive">
            „Braucht dich“ konnte nicht geladen werden.
          </span>
          <Button
            variant="outline"
            size="sm"
            className="max-md:min-h-11"
            data-testid={NEEDS_YOU_TEST_IDS.retry}
            onClick={() => void query.refetch()}
            disabled={query.isFetching}
            componentId="inbox.needs_you.retry"
          >
            {query.isFetching ? "Lädt…" : "Erneut versuchen"}
          </Button>
        </div>
      )}

      {/* Empty: heading only; the inbox empty state below stays the single next action. */}

      {rows.length > 0 && (
        <ul className="flex flex-col">
          {rows.map((row) =>
            row.kind === "approval" ? (
              <NeedsYouItem
                key={row.key}
                kind="approval"
                to={approvalPath(row.approval)}
                icon={MessageCircleQuestionMarkIcon}
                title="Agent braucht eine Antwort"
                place={conversationLabel(row.approval.channel_kind, row.approval.channel_name)}
                detail={openApprovalsLabel(row.approval.pending_count)}
              />
            ) : (
              <NeedsYouItem
                key={row.key}
                kind="mention"
                to={mentionPath(row.mention)}
                icon={AtSignIcon}
                title={`${row.mention.author_label} hat dich erwähnt`}
                place={conversationLabel(row.mention.channel_kind, row.mention.channel_name)}
                detail={row.mention.thread_root_id ? "im Thread" : undefined}
                createdAt={row.mention.created_at}
              />
            ),
          )}
        </ul>
      )}
    </section>
  );
}

function NeedsYouItem({
  kind,
  to,
  icon: Icon,
  title,
  place,
  detail,
  createdAt,
}: {
  kind: "approval" | "mention";
  to: string;
  icon: LucideIcon;
  title: string;
  place: string;
  detail?: string;
  /** Epoch microseconds. */
  createdAt?: number;
}) {
  const date = createdAt === undefined ? null : new Date(toEpochMs(createdAt));
  return (
    <li data-testid={NEEDS_YOU_TEST_IDS.item} data-kind={kind}>
      <Link
        to={to}
        componentId={`inbox.needs_you.open_${kind}`}
        className="flex min-h-11 min-w-0 items-start gap-3 rounded-lg px-3 py-2 hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring active:bg-muted"
      >
        <span
          className={cn(
            "flex size-8 shrink-0 items-center justify-center rounded-lg",
            kind === "approval"
              ? "border border-warning/50 bg-warning/15 text-foreground"
              : "bg-muted text-muted-foreground",
          )}
        >
          <Icon aria-hidden className="size-4" />
        </span>
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="min-w-0 text-sm font-medium text-pretty wrap-anywhere">{title}</span>
          <span className="flex min-w-0 flex-wrap gap-x-2 text-xs text-muted-foreground">
            <span className="min-w-0 wrap-anywhere">{place}</span>
            {detail && <span className="tabular-nums">{detail}</span>}
          </span>
        </span>
        {date && (
          <time
            dateTime={date.toISOString()}
            className="shrink-0 pt-0.5 text-xs text-muted-foreground tabular-nums"
          >
            {formatMessageTime(date.getTime())}
          </time>
        )}
      </Link>
    </li>
  );
}

function isWindowFocused(): boolean {
  if (typeof document === "undefined") return true;
  return typeof document.hasFocus === "function" ? document.hasFocus() : true;
}

/** Channel id of the open `/chat/:channelId[...]` route, or null. */
export function openChannelId(pathname: string): string | null {
  const match = /^\/chat\/([^/]+)/.exec(pathname);
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}

/**
 * Notify only while the window is unfocused or the conversation is not open
 * (FR-011).
 */
export function shouldNotifyFor(
  channelId: string,
  pathname: string,
  focused = isWindowFocused(),
): boolean {
  return !focused || openChannelId(pathname) !== channelId;
}

/**
 * OS notifications for team chat: new mentions, new DM messages, and new
 * agent approvals the caller may answer. Seeds from the first data it sees,
 * so nothing already waiting at mount fires. Mount once.
 */
export function useNeedsYouNotifications(enabled: boolean): void {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const pathRef = useRef(pathname);
  pathRef.current = pathname;
  const { data } = useNeedsYou(enabled);
  const bootstrap = useQuery({
    queryKey: teamChatQueryKeys.bootstrap,
    queryFn: ({ signal }) => fetchBootstrap(signal),
    enabled,
  });
  const bootstrapRef = useRef<BootstrapResponse | undefined>(bootstrap.data);
  bootstrapRef.current = bootstrap.data;

  const seenMentions = useRef<Set<string> | null>(null);
  const seenApprovals = useRef<Map<string, number> | null>(null);

  useEffect(() => {
    if (!enabled || !data) return;
    const notify = (event: ChatNotificationEvent, path: string, tag: string, channelId: string) => {
      if (!shouldNotifyFor(channelId, pathRef.current)) return;
      showChatNotification(event, { tag, navigatePath: path, onClick: () => navigate(path) });
    };

    const firstRun = seenMentions.current === null || seenApprovals.current === null;
    const mentions = seenMentions.current ?? new Set<string>();
    const approvals = seenApprovals.current ?? new Map<string, number>();
    for (const mention of data.mentions) {
      if (mentions.has(mention.message_id)) continue;
      mentions.add(mention.message_id);
      // A DM message notifies as a DM, also when it mentions the caller.
      if (firstRun || mention.channel_kind === "dm") continue;
      notify(
        {
          kind: "mention",
          channelName: conversationLabel(mention.channel_kind, mention.channel_name),
        },
        mentionPath(mention),
        `omnigent:chat:${mention.message_id}`,
        mention.channel_id,
      );
    }
    const live = new Set<string>();
    for (const approval of data.approvals) {
      live.add(approval.session_id);
      const before = approvals.get(approval.session_id) ?? 0;
      approvals.set(approval.session_id, approval.pending_count);
      if (firstRun || approval.pending_count <= before) continue;
      // Same tag as the session notifications, so one session never stacks two toasts.
      notify(
        { kind: "approval" },
        approvalPath(approval),
        `omnigent:session:${approval.session_id}`,
        approval.channel_id,
      );
    }
    for (const sessionId of [...approvals.keys()]) {
      if (!live.has(sessionId)) approvals.delete(sessionId);
    }
    seenMentions.current = mentions;
    seenApprovals.current = approvals;
  }, [data, enabled, navigate]);

  // DM messages arrive as `message.created` frames with the author id.
  // Backfilled changes carry no author and never notify.
  useEffect(() => {
    if (!enabled) return;
    return useTeamChatStore.subscribe((state, prev) => {
      if (state.changes === prev.changes) return;
      const boot = bootstrapRef.current;
      if (!boot) return;
      for (const [key, change] of Object.entries(state.changes)) {
        if (Object.hasOwn(prev.changes, key)) continue;
        if (!isDmMessageFromOther(change, boot)) continue;
        const path = chatPath(change.channel_id, change.thread_root_id);
        if (!shouldNotifyFor(change.channel_id, pathRef.current)) continue;
        showChatNotification(
          { kind: "dm", authorName: chatPersonName(change.author_id ?? "", boot.directory) },
          { tag: `omnigent:chat:${change.id}`, navigatePath: path, onClick: () => navigate(path) },
        );
      }
    });
  }, [enabled, navigate]);
}

function isDmMessageFromOther(change: TeamChatChange, boot: BootstrapResponse): boolean {
  if (change.kind !== "message" || !change.author_id || change.author_id === boot.me) return false;
  return boot.dms.some((dm) => dm.id === change.channel_id);
}
