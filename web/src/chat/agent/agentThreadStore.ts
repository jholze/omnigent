/**
 * Agent-Thread state for the team chat (FR-044, FR-047).
 *
 * Sources, all body-less:
 * - `agent_state` and `session_id` on stored thread-root messages: the last
 *   known state and session on a cold load, before any frame (AC-047b). The
 *   session id lets a reloaded view mount the live chip and approval card.
 * - `thread.updated` chat frames: `chat_threads` state per root message
 *   (`agent_state`, `session_id`). Every channel member gets these, with or
 *   without a grant on the session (AC-047b).
 * - Session rows from `WS /v1/sessions/updates`: `status` and
 *   `pending_elicitations_count`. The server only sends rows the viewer may
 *   read, so a viewer without a grant never gets one.
 *
 * Views register the session ids they show (`setWatched`); the
 * SessionUpdatesProvider adds them to the socket watch-set. Nothing here
 * watches every session in the account.
 */
import { useEffect } from "react";
import { create } from "zustand";

import type { SessionListWireItem } from "@/lib/sessionListCache";
import type { ChatThreadFrame } from "@/lib/sessionUpdatesSocket";
import type { ChannelAgentThread, ChatMessage } from "../chatApi";

/** Session label the server stamps on an Agent-Thread session. */
export const CHAT_THREAD_LABEL_KEY = "omnigent.chat.v1";

export interface AgentThreadInfo {
  channelId: string;
  rootId: string;
  /** `chat_threads.agent_state`; `null` when only the session label is known. */
  agentState: string | null;
  sessionId: string | null;
}

export interface AgentSessionInfo {
  /** `SessionListItem.status` (`idle`/`running`/`failed`, or `launching`/`waiting`). */
  status: string | null;
  pendingElicitations: number;
  permissionLevel: number | null;
}

interface AgentThreadStoreState {
  threads: Record<string, AgentThreadInfo>;
  sessions: Record<string, AgentSessionInfo>;
  /** Session ids on screen, per view key (`channel:<id>`, `thread:<root>`). */
  watchers: Record<string, string[]>;
  applyThreadFrame: (frame: ChatThreadFrame) => void;
  seedFromMessages: (messages: readonly ChatMessage[]) => void;
  /** Active agent threads from `GET .../agent-threads`, including off-page roots. */
  seedFromChannelThreads: (channelId: string, threads: readonly ChannelAgentThread[]) => void;
  applySessionItems: (items: SessionListWireItem[]) => void;
  dropSessions: (ids: string[]) => void;
  setWatched: (key: string, ids: string[]) => void;
}

/** `{"channel_id": "...", "thread_root_id": "..."}` from the session label. */
export function parseChatThreadLabel(
  labels: Record<string, string | null | undefined> | null | undefined,
): { channelId: string; rootId: string } | null {
  const raw = labels?.[CHAT_THREAD_LABEL_KEY];
  if (typeof raw !== "string" || raw === "") return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null) return null;
    const record = value as Record<string, unknown>;
    if (typeof record.channel_id !== "string" || record.channel_id === "") return null;
    if (typeof record.thread_root_id !== "string" || record.thread_root_id === "") return null;
    return { channelId: record.channel_id, rootId: record.thread_root_id };
  } catch {
    return null;
  }
}

function sameStrings(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

function sessionInfoOf(item: SessionListWireItem): AgentSessionInfo {
  const pending = item.pending_elicitations_count;
  return {
    status: typeof item.status === "string" ? item.status : null,
    pendingElicitations: typeof pending === "number" && Number.isFinite(pending) ? pending : 0,
    permissionLevel: typeof item.permission_level === "number" ? item.permission_level : null,
  };
}

function pageAgentState(raw: string | null | undefined): string | null {
  return typeof raw === "string" && raw !== "" && raw !== "none" ? raw : null;
}

function pageSessionId(raw: string | null | undefined): string | null {
  return typeof raw === "string" && raw !== "" ? raw : null;
}

/**
 * Merge one stored thread into the map.
 *
 * A frame that already set `agentState` or `sessionId` wins. A null
 * `agentState` is still empty, so a later page can fill it. Returns the
 * same map when nothing changed.
 */
function absorbThreadSeed(
  threads: Record<string, AgentThreadInfo>,
  seed: {
    channelId: string;
    rootId: string;
    agentState: string | null;
    sessionId: string | null;
  },
): Record<string, AgentThreadInfo> {
  if (seed.rootId === "" || (seed.agentState === null && seed.sessionId === null)) return threads;
  const current = threads[seed.rootId];
  const agentState = current?.agentState ?? seed.agentState;
  const sessionId = current?.sessionId ?? seed.sessionId;
  if (
    current !== undefined &&
    current.agentState === agentState &&
    current.sessionId === sessionId
  ) {
    return threads;
  }
  return {
    ...threads,
    [seed.rootId]: {
      channelId: seed.channelId,
      rootId: seed.rootId,
      agentState,
      sessionId,
    },
  };
}

function sameSession(a: AgentSessionInfo | undefined, b: AgentSessionInfo): boolean {
  return (
    a !== undefined &&
    a.status === b.status &&
    a.pendingElicitations === b.pendingElicitations &&
    a.permissionLevel === b.permissionLevel
  );
}

export const useAgentThreadStore = create<AgentThreadStoreState>((set, get) => ({
  threads: {},
  sessions: {},
  watchers: {},
  applyThreadFrame: (frame) => {
    const current = get().threads[frame.root_message_id];
    const next: AgentThreadInfo = {
      channelId: frame.channel_id,
      rootId: frame.root_message_id,
      agentState: frame.agent_state,
      // A frame without a session (still starting) keeps a label-known id.
      sessionId: frame.session_id ?? current?.sessionId ?? null,
    };
    if (
      current &&
      current.channelId === next.channelId &&
      current.agentState === next.agentState &&
      current.sessionId === next.sessionId
    ) {
      return;
    }
    set({ threads: { ...get().threads, [next.rootId]: next } });
  },
  seedFromMessages: (messages) => {
    const threads = get().threads;
    let next = threads;
    for (const message of messages) {
      if (message.thread_root_id !== null) continue;
      next = absorbThreadSeed(next, {
        channelId: message.channel_id,
        rootId: message.id,
        agentState: pageAgentState(message.agent_state),
        sessionId: pageSessionId(message.session_id),
      });
    }
    if (next !== threads) set({ threads: next });
  },
  seedFromChannelThreads: (channelId, rows) => {
    const threads = get().threads;
    let next = threads;
    for (const row of rows) {
      next = absorbThreadSeed(next, {
        channelId,
        rootId: row.root_message_id,
        agentState: pageAgentState(row.agent_state),
        sessionId: pageSessionId(row.session_id),
      });
    }
    if (next !== threads) set({ threads: next });
  },
  applySessionItems: (items) => {
    const { threads, sessions, watchers } = get();
    const watched = new Set(Object.values(watchers).flat());
    let nextThreads = threads;
    let nextSessions = sessions;
    for (const item of items) {
      const label = parseChatThreadLabel(item.labels);
      if (label === null && !watched.has(item.id)) continue;
      if (label !== null) {
        const known = nextThreads[label.rootId];
        if (known === undefined || known.sessionId === null) {
          nextThreads = {
            ...nextThreads,
            [label.rootId]: {
              channelId: label.channelId,
              rootId: label.rootId,
              agentState: known?.agentState ?? null,
              sessionId: item.id,
            },
          };
        }
      }
      const info = sessionInfoOf(item);
      if (!sameSession(nextSessions[item.id], info)) {
        nextSessions = { ...nextSessions, [item.id]: info };
      }
    }
    if (nextThreads !== threads || nextSessions !== sessions) {
      set({ threads: nextThreads, sessions: nextSessions });
    }
  },
  dropSessions: (ids) => {
    const sessions = get().sessions;
    const drop = new Set(ids);
    if (!ids.some((id) => Object.hasOwn(sessions, id))) return;
    set({ sessions: Object.fromEntries(Object.entries(sessions).filter(([id]) => !drop.has(id))) });
  },
  setWatched: (key, ids) => {
    const watchers = get().watchers;
    const sorted = [...new Set(ids.filter((id) => id !== ""))].sort();
    const current = watchers[key];
    if (sorted.length === 0) {
      if (current === undefined) return;
      set({ watchers: Object.fromEntries(Object.entries(watchers).filter(([k]) => k !== key)) });
      return;
    }
    if (current !== undefined && sameStrings(current, sorted)) return;
    set({ watchers: { ...watchers, [key]: sorted } });
  },
}));

/** Union of every registered on-screen Agent-Thread session id, sorted. */
export function watchedAgentSessionIds(
  state: Pick<AgentThreadStoreState, "watchers"> = useAgentThreadStore.getState(),
): string[] {
  return [...new Set(Object.values(state.watchers).flat())].sort();
}

/** Record a parsed `thread.updated` frame. */
export function applyAgentThreadFrame(frame: ChatThreadFrame): void {
  useAgentThreadStore.getState().applyThreadFrame(frame);
}

/** Seed thread state from the `agent_state` of loaded thread roots (AC-047b). */
export function useSeedAgentThreads(messages: readonly ChatMessage[] | undefined): void {
  useEffect(() => {
    if (messages !== undefined) useAgentThreadStore.getState().seedFromMessages(messages);
  }, [messages]);
}

/** Seed active agent threads, including roots outside the loaded tail (AC-107b). */
export function useSeedChannelAgentThreads(
  channelId: string,
  threads: readonly ChannelAgentThread[] | undefined,
): void {
  useEffect(() => {
    if (threads !== undefined) {
      useAgentThreadStore.getState().seedFromChannelThreads(channelId, threads);
    }
  }, [channelId, threads]);
}

/** Keep `ids` in the session watch-set while the calling view is mounted. */
export function useWatchAgentSessions(key: string, ids: readonly string[]): void {
  const joined = [...ids].sort().join(",");
  useEffect(() => {
    const store = useAgentThreadStore.getState();
    store.setWatched(key, joined === "" ? [] : joined.split(","));
    return () => useAgentThreadStore.getState().setWatched(key, []);
  }, [key, joined]);
}
