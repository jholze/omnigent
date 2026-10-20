/**
 * Chip state for one Agent-Thread root.
 *
 * Source order: the open thread's live session stream (registry entry,
 * FR-044) → the session row from the updates socket (FR-047) → the last
 * `chat_threads` state from `thread.updated` (AC-047b).
 */
import { useCallback, useMemo, useSyncExternalStore } from "react";

import { conversationRegistry } from "@/store/conversationRegistry";
import { agentChipState, type AgentChipState } from "./agentStatus";
import { useAgentThreadStore } from "./agentThreadStore";

/**
 * `"<status>|<open cards>"` from a bound registry entry, else `null`.
 * A string keeps the snapshot stable while text streams in.
 */
function liveStatusKey(sessionId: string | null): string | null {
  if (sessionId === null) return null;
  const state = conversationRegistry.peek(sessionId)?.getState();
  if (state === undefined || state.loadingConversation || state.conversationLoadError !== null) {
    return null;
  }
  const pending = state.blocks.filter(
    (block) => block.type === "elicitation" && block.status === "pending",
  ).length;
  return `${state.sessionStatus}|${pending}`;
}

export function useLiveSessionStatus(
  sessionId: string | null,
): { status: string; pending: number } | null {
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (sessionId === null) return () => {};
      return conversationRegistry.subscribe((id) => {
        if (id === sessionId) onChange();
      });
    },
    [sessionId],
  );
  const key = useSyncExternalStore(subscribe, () => liveStatusKey(sessionId));
  return useMemo(() => {
    if (key === null) return null;
    const [status, pending] = key.split("|");
    return { status, pending: Number(pending) };
  }, [key]);
}

export function useAgentThreadChip(rootId: string): AgentChipState | null {
  const thread = useAgentThreadStore((state) => state.threads[rootId]);
  const sessionId = thread?.sessionId ?? null;
  const row = useAgentThreadStore((state) =>
    sessionId === null ? undefined : state.sessions[sessionId],
  );
  const live = useLiveSessionStatus(sessionId);
  return useMemo(() => {
    if (thread === undefined) return null;
    return agentChipState({
      agentState: thread.agentState,
      sessionStatus: live?.status ?? row?.status ?? null,
      pendingElicitations: live?.pending ?? row?.pendingElicitations ?? 0,
    });
  }, [thread, live, row]);
}
