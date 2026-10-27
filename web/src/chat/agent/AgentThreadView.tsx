/**
 * Live part of an open Agent-Thread (FR-045, FR-055 … FR-058).
 *
 * Shown under the stored thread replies. With READ on the session it binds
 * that one session in the conversation registry (one open thread, one
 * stream; R-6) and renders the current turn with AI Elements
 * (`MessageResponse`, `Reasoning`) plus approval cards at the position the
 * agent asked. When the turn ends, the server mirrors the final answer into
 * the thread as a stored reply, and the live turn steps back once that reply
 * is in. Error items are never rendered: the thread gets one system line
 * from the server instead (AC-044b).
 *
 * Without a grant nothing is streamed: a hint names who can watch live, and
 * the stored (mirrored) replies above stay the record (AC-045b).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowUpRightIcon, CircleAlertIcon, EyeOffIcon } from "lucide-react";

import { MessageResponse } from "@/components/ai-elements/message";
import { ElicitationCard, type SubmitApprovalFn } from "@/components/blocks/ApprovalCard";
import { ReasoningView } from "@/components/blocks/ReasoningView";
import {
  computeIsTurnActive,
  computeIsWorking,
  reorderCommittedRequestElicitations,
} from "@/components/chat/chatBubbleParts";
import { ConversationScopeContext } from "@/components/chat/conversationScope";
import { Button } from "@/components/ui/button";
import { useConversationEntryState } from "@/hooks/useConversationEntryState";
import { useSession } from "@/hooks/useSession";
import { LEVEL_EDIT } from "@/lib/permissionsApi";
import { buildBubbles, createBubbleCache, type Bubble, type RenderItem } from "@/lib/renderItems";
import { Link } from "@/lib/routing";
import { ApiError } from "@/lib/sessionsApi";
import { ensureConversationStreamed, releaseConversation, useChatStore } from "@/store/chatStore";
import { conversationRegistry } from "@/store/conversationRegistry";
import type { DirectoryEntry } from "../chatApi";
import { MessageAvatar, principalLabel } from "../MessageItem";
import {
  CHAT_APPROVAL_LABELS,
  READ_ONLY_NOTE,
  SEND_FAILED_TEXT,
  SENDING_NOTE,
  submitThreadApproval,
  useThreadApprovals,
} from "./agentApprovals";
import { useStatusAfterConnect } from "./useStatusAfterConnect";
import { CardMount } from "./CardMount";
import { CHAT_TEST_IDS } from "../testids";

/** Author id of mirrored agent replies (`chat_agent_threads._AUTHOR_AGENT`). */
export const AGENT_AUTHOR_ID = "agent";

export type SessionAccess = "loading" | "none" | "read" | "edit" | "error";

/** READ / EDIT from the session snapshot; 403 or 404 means no grant. */
export function sessionAccessOf(
  session: { permissionLevel: number | null } | null,
  isLoading: boolean,
  error: Error | null,
): SessionAccess {
  if (session !== null) {
    const level = session.permissionLevel;
    // `null`: permissions are off on this server, everyone may act.
    if (level === null || level >= LEVEL_EDIT) return "edit";
    return level >= 1 ? "read" : "none";
  }
  if (error instanceof ApiError && (error.status === 403 || error.status === 404)) return "none";
  if (error !== null) return "error";
  return isLoading ? "loading" : "error";
}

type ElicitationItem = Extract<RenderItem, { kind: "elicitation" }>;
type ShownItem = Extract<RenderItem, { kind: "text" | "reasoning" | "elicitation" }>;

function isShown(item: RenderItem): item is ShownItem {
  return item.kind === "text" || item.kind === "reasoning" || item.kind === "elicitation";
}

function isPendingCard(item: RenderItem): item is ElicitationItem {
  return item.kind === "elicitation" && item.status === "pending";
}

/**
 * The current turn: assistant output after the last user input, plus any
 * still-open card from an earlier turn (it stays where the agent asked).
 */
export function currentTurn(bubbles: Bubble[]): { key: string | null; items: ShownItem[] } {
  let lastUser = -1;
  for (let index = 0; index < bubbles.length; index += 1) {
    if (bubbles[index].kind === "user") lastUser = index;
  }
  const earlierOpen: ShownItem[] = [];
  const items: ShownItem[] = [];
  let key: string | null = null;
  for (let index = 0; index < bubbles.length; index += 1) {
    const bubble = bubbles[index];
    if (bubble.kind !== "assistant") continue;
    if (index < lastUser) {
      earlierOpen.push(...bubble.items.filter(isPendingCard));
      continue;
    }
    if (key === null) key = bubble.stableId;
    items.push(...bubble.items.filter(isShown));
  }
  return { key, items: [...earlierOpen, ...items] };
}

/**
 * Name of the user the server reported as the decider, for a card resolved
 * elsewhere. A directory display name wins; otherwise the id shows.
 */
export function decidedByLabel(
  item: ElicitationItem,
  directory: Map<string, DirectoryEntry>,
): string | null {
  const decidedBy = item.response?.decidedBy;
  if (typeof decidedBy !== "string" || decidedBy === "") return null;
  return principalLabel(decidedBy, directory);
}

function itemKey(item: ShownItem, index: number): string {
  if (item.kind === "elicitation") return `elicitation:${item.elicitationId}`;
  return `${item.kind}:${item.itemId ?? index}`;
}

interface LiveAgentOutputProps {
  sessionId: string;
  canResolve: boolean;
  me: string;
  directory: Map<string, DirectoryEntry>;
  /** Stored agent and system replies in this thread; a new one ends a held turn. */
  agentReplyCount: number;
}

function LiveAgentOutput({
  sessionId,
  canResolve,
  me,
  directory,
  agentReplyCount,
}: LiveAgentOutputProps) {
  // Bind only this session. A stream this view opened closes with it, so
  // closed threads hold no connection (HTTP/1.1 budget, R-6).
  useEffect(() => {
    const wasLive = conversationRegistry.has(sessionId);
    void ensureConversationStreamed(sessionId);
    return () => {
      if (wasLive || useChatStore.getState().conversationId === sessionId) return;
      releaseConversation(sessionId);
    };
  }, [sessionId]);

  const state = useConversationEntryState(sessionId);
  const {
    blocks,
    activeResponse,
    interruptedResponseIds,
    sessionStatus,
    loadingConversation,
    conversationLoadError,
  } = state;
  useStatusAfterConnect(sessionId, loadingConversation);
  const cacheRef = useRef(createBubbleCache());
  const bubbles = useMemo(
    () =>
      reorderCommittedRequestElicitations(
        buildBubbles(
          blocks,
          activeResponse,
          cacheRef.current,
          interruptedResponseIds,
          computeIsWorking(sessionStatus),
        ),
      ),
    [blocks, activeResponse, interruptedResponseIds, sessionStatus],
  );
  const turn = useMemo(() => currentTurn(bubbles), [bubbles]);
  const working = computeIsTurnActive(sessionStatus, state.status === "streaming");
  const live = working || turn.items.some(isPendingCard);

  // After the turn ends the mirrored reply replaces it. Keep the turn on
  // screen until that stored reply arrives, so the answer does not blink.
  const [held, setHeld] = useState<{ key: string; replies: number } | null>(null);
  if (live && turn.key !== null && (held?.key !== turn.key || held.replies !== agentReplyCount)) {
    setHeld({ key: turn.key, replies: agentReplyCount });
  }
  const show =
    live ||
    (turn.key !== null &&
      held !== null &&
      held.key === turn.key &&
      agentReplyCount <= held.replies);

  // Cards already there when the live view finished loading are history and
  // mount static; a card that arrives later springs in (FR-078, cardMount).
  const [initialCardIds, setInitialCardIds] = useState<Set<string> | null>(null);
  if (initialCardIds === null && !loadingConversation) {
    setInitialCardIds(
      new Set(turn.items.flatMap((item) => ("elicitationId" in item ? [item.elicitationId] : []))),
    );
  }

  const approvals = useThreadApprovals();
  const meLabel = principalLabel(me, directory);
  const submit: SubmitApprovalFn = (elicitationId, action, content, meta) => {
    if (action !== "accept" && action !== "decline") return;
    void submitThreadApproval(sessionId, elicitationId, action, meLabel, content, meta);
  };

  if (conversationLoadError !== null) {
    return (
      <LiveNotice
        tone="error"
        text="Live-Ansicht nicht verfügbar."
        onRetry={() => void ensureConversationStreamed(sessionId)}
      />
    );
  }
  if (loadingConversation && blocks.length === 0) {
    return <LiveNotice tone="busy" text="Live-Ansicht lädt…" />;
  }
  if (!show) return null;

  const lastIndex = turn.items.length - 1;
  const hasText = turn.items.some((item) => item.kind === "text" && item.text.trim() !== "");
  return (
    <ConversationScopeContext.Provider value={sessionId}>
      <section
        aria-label="Live-Ausgabe des Agenten"
        data-testid="agent-thread-live"
        aria-busy={working || undefined}
        className="flex gap-3 px-4 pt-2 pb-3"
      >
        <div className="flex w-7 shrink-0 justify-center">
          <MessageAvatar label={principalLabel(AGENT_AUTHOR_ID, directory)} agent />
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
            <span className="min-w-0 text-sm font-medium break-words">
              {principalLabel(AGENT_AUTHOR_ID, directory)}
            </span>
            <span className="text-xs text-muted-foreground">
              {working ? "schreibt gerade" : "live"}
            </span>
          </div>
          {turn.items.map((item, index) => {
            const key = itemKey(item, index);
            if (item.kind === "text") {
              return (
                <div key={key} className="min-w-0 text-sm text-pretty">
                  <MessageResponse>{item.text}</MessageResponse>
                </div>
              );
            }
            if (item.kind === "reasoning") {
              return (
                <ReasoningView
                  key={key}
                  text={item.text}
                  isStreaming={working && index === lastIndex}
                  duration={item.duration}
                />
              );
            }
            const id = item.elicitationId;
            let note: string | null = null;
            if (!canResolve || approvals.forbidden[id]) note = READ_ONLY_NOTE;
            else if (approvals.sending[id]) note = SENDING_NOTE;
            return (
              // Phone: the card's buttons reach the 44 px touch target (NFR-032).
              <CardMount
                key={key}
                animate={
                  initialCardIds !== null && !initialCardIds.has(id) && item.status === "pending"
                }
                className="flex min-w-0 flex-col gap-1 max-md:[&_button]:min-h-11"
              >
                <ElicitationCard
                  item={item}
                  onSubmit={submit}
                  labels={CHAT_APPROVAL_LABELS}
                  resolvedBy={approvals.resolvers[id] ?? decidedByLabel(item, directory)}
                  readOnlyNote={note}
                />
                {approvals.failed[id] && item.status === "pending" ? (
                  <p role="alert" className="flex items-center gap-1 text-xs text-destructive">
                    <CircleAlertIcon className="size-4 shrink-0" aria-hidden="true" />
                    {SEND_FAILED_TEXT}
                  </p>
                ) : null}
              </CardMount>
            );
          })}
          {working && !hasText ? (
            <p className="text-sm text-muted-foreground">Agent arbeitet…</p>
          ) : null}
        </div>
      </section>
    </ConversationScopeContext.Provider>
  );
}

function LiveNotice({
  tone,
  text,
  onRetry,
}: {
  tone: "busy" | "error" | "hint";
  text: string;
  onRetry?: () => void;
}) {
  return (
    <div
      data-testid="agent-live-notice"
      aria-busy={tone === "busy" || undefined}
      className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 px-4 pt-1 pb-3 text-xs text-muted-foreground"
    >
      {tone === "hint" ? <EyeOffIcon className="size-4 shrink-0" aria-hidden="true" /> : null}
      {tone === "error" ? <CircleAlertIcon className="size-4 shrink-0" aria-hidden="true" /> : null}
      <span className="min-w-0 break-words">{text}</span>
      {onRetry ? (
        <Button
          type="button"
          variant="link"
          size="xs"
          componentId="chat.agent.live.retry"
          className="min-h-11 px-1 text-xs md:min-h-0"
          onClick={onRetry}
        >
          Erneut versuchen
        </Button>
      ) : null}
    </div>
  );
}

/**
 * "Session öffnen" in the Thread header (FR-085, AC-085a). The main sidebar
 * lists an Agent-Thread session for its owner only; everyone else with a
 * grant opens it here. Without a grant the link does not render: the server
 * would refuse the session anyway.
 */
export function OpenAgentSessionLink({ sessionId }: { sessionId: string }) {
  const { session, isLoading, error } = useSession(sessionId);
  const access = sessionAccessOf(session, isLoading, error);
  if (access !== "read" && access !== "edit") return null;
  return (
    <Link
      to={`/c/${encodeURIComponent(sessionId)}`}
      componentId="chat.thread.open_session"
      data-testid={CHAT_TEST_IDS.threadOpenSession}
      className="relative inline-flex min-h-11 shrink-0 items-center gap-1 rounded-md px-2 text-xs text-muted-foreground before:absolute before:inset-x-0 before:-inset-y-1.5 before:content-[''] hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring md:min-h-8"
    >
      Session öffnen
      <ArrowUpRightIcon className="size-3.5 shrink-0" aria-hidden="true" />
    </Link>
  );
}

export interface AgentThreadViewProps {
  sessionId: string;
  me: string;
  directory: Map<string, DirectoryEntry>;
  /** Who started the agent, for the no-grant hint. `null`: not known here. */
  ownerLabel: string | null;
  agentReplyCount: number;
  /** The server already stored its "Live-Ansicht nur für …" line in this thread. */
  sharingNoticeStored: boolean;
}

export function AgentThreadView({
  sessionId,
  me,
  directory,
  ownerLabel,
  agentReplyCount,
  sharingNoticeStored,
}: AgentThreadViewProps) {
  const queryClient = useQueryClient();
  const { session, isLoading, error } = useSession(sessionId);
  const access = sessionAccessOf(session, isLoading, error);

  if (access === "loading") return <LiveNotice tone="busy" text="Live-Ansicht lädt…" />;
  if (access === "error") {
    return (
      <LiveNotice
        tone="error"
        text="Live-Ansicht nicht verfügbar."
        onRetry={() => void queryClient.invalidateQueries({ queryKey: ["session", sessionId] })}
      />
    );
  }
  if (access === "none") {
    if (sharingNoticeStored) return null;
    const who = ownerLabel ?? "die Person, die den Agenten gestartet hat";
    return <LiveNotice tone="hint" text={`Live-Ansicht nur für ${who}`} />;
  }
  return (
    <LiveAgentOutput
      sessionId={sessionId}
      canResolve={access === "edit"}
      me={me}
      directory={directory}
      agentReplyCount={agentReplyCount}
    />
  );
}
