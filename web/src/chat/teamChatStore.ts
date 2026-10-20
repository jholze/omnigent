/**
 * Cursor and deduped change set for team chat.
 *
 * TanStack Query owns bootstrap and channel messages. This store owns the
 * backfill cursor and one row per change id.
 */
import { create } from "zustand";

import type { ChatChangeFrame } from "@/lib/sessionUpdatesSocket";

export interface TeamChatChange {
  id: string;
  kind: "message" | "read";
  channel_id: string;
  thread_root_id: string | null;
  created_at: number;
  author_id?: string;
}

interface TeamChatState {
  changesCursor: string | null;
  changes: Record<string, TeamChatChange>;
  pendingChannels: string[];
  /** Keep the first cursor. A later bootstrap must not jump past a backfill. */
  seedCursor: (cursor: string) => void;
  replaceCursor: (cursor: string) => void;
  /**
   * Insert changes that are not already stored. Returns channel ids of newly
   * inserted messages so the caller can fill the message cache once.
   */
  mergeChanges: (items: TeamChatChange[]) => string[];
  noteChannels: (channelIds: string[]) => void;
  takePendingChannels: () => string[];
}

const MESSAGE_KINDS = new Set(["message", "message.created"]);
const READ_KINDS = new Set(["read", "read.updated"]);

export function teamChatChangeKey(change: Pick<TeamChatChange, "kind" | "id">): string {
  return `${change.kind}:${change.id}`;
}

export function normalizeChangeItem(value: unknown): TeamChatChange | null {
  if (typeof value !== "object" || value === null) return null;
  const item = value as Record<string, unknown>;
  if (typeof item.kind !== "string" || typeof item.id !== "string" || item.id === "") return null;
  if (typeof item.channel_id !== "string" || item.channel_id === "") return null;
  if (typeof item.created_at !== "number" || !Number.isFinite(item.created_at)) return null;
  const thread = item.thread_root_id;
  if (thread !== null && typeof thread !== "string") return null;
  let kind: TeamChatChange["kind"] | null = null;
  if (MESSAGE_KINDS.has(item.kind)) kind = "message";
  else if (READ_KINDS.has(item.kind)) kind = "read";
  if (kind === null) return null;
  return {
    id: item.id,
    kind,
    channel_id: item.channel_id,
    thread_root_id: typeof thread === "string" ? thread : null,
    created_at: item.created_at,
  };
}

export const useTeamChatStore = create<TeamChatState>((set, get) => ({
  changesCursor: null,
  changes: {},
  pendingChannels: [],
  seedCursor: (cursor) => {
    if (get().changesCursor !== null) return;
    set({ changesCursor: cursor });
  },
  replaceCursor: (cursor) => {
    set({ changesCursor: cursor });
  },
  mergeChanges: (items) => {
    const current = get().changes;
    const next = { ...current };
    const addedChannels: string[] = [];
    let changed = false;
    for (const item of items) {
      const key = teamChatChangeKey(item);
      if (Object.hasOwn(next, key)) continue;
      next[key] = item;
      changed = true;
      if (item.kind === "message") addedChannels.push(item.channel_id);
    }
    if (changed) set({ changes: next });
    return addedChannels;
  },
  noteChannels: (channelIds) => {
    if (channelIds.length === 0) return;
    const pending = new Set(get().pendingChannels);
    const before = pending.size;
    for (const channelId of channelIds) pending.add(channelId);
    if (pending.size === before) return;
    set({ pendingChannels: [...pending] });
  },
  takePendingChannels: () => {
    const pending = get().pendingChannels;
    if (pending.length === 0) return pending;
    set({ pendingChannels: [] });
    return pending;
  },
}));

/** Record one parsed chat frame. A repeat of the same id is a no-op. */
export function applyChatFrame(frame: ChatChangeFrame): void {
  const change =
    frame.kind === "message.created"
      ? {
          id: frame.message_id,
          kind: "message" as const,
          channel_id: frame.channel_id,
          thread_root_id: frame.thread_root_id,
          created_at: frame.created_at,
          author_id: frame.author_id,
        }
      : {
          id: frame.id,
          kind: "read" as const,
          channel_id: frame.channel_id,
          thread_root_id: frame.thread_root_id,
          created_at: frame.created_at,
        };
  const added = useTeamChatStore.getState().mergeChanges([change]);
  useTeamChatStore.getState().noteChannels(added);
}
