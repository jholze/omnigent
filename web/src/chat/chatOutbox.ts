/**
 * Optimistic sends for team chat (FR-084, NFR-002).
 *
 * A send shows its row at once with a fresh `client_id`. On a network error,
 * 429 or 5xx the row stays as "nicht gesendet"; "erneut senden" posts the same
 * `client_id` and body again, so the server stores at most one message (201
 * first time, 200 on replay). Bodies stay in memory and are never logged.
 */
import type { QueryClient } from "@tanstack/react-query";
import { create } from "zustand";

import {
  CLIENT_ID_PATTERN,
  ChatApiError,
  fetchBootstrap,
  postMessage,
  teamChatQueryKeys,
  type BootstrapResponse,
  type ChatMessage,
  type MessagePageResponse,
} from "./chatApi";
import { fetchLatest } from "./freshQuery";
import { useTeamChatStore } from "./teamChatStore";

export type OutboxStatus = "sending" | "failed";

export interface OutboxEntry {
  clientId: string;
  channelId: string;
  threadRootId: string | null;
  authorId: string;
  body: string;
  createdAtMs: number;
  status: OutboxStatus;
  /**
   * Set when the last send was answered 429. Seconds from `Retry-After`, or
   * null when the header was missing. Cleared on the next attempt.
   */
  rateLimited?: { retryAfterSeconds: number | null } | null;
}

interface OutboxState {
  entries: OutboxEntry[];
  add: (entry: OutboxEntry) => void;
  setStatus: (
    clientId: string,
    status: OutboxStatus,
    rateLimited?: OutboxEntry["rateLimited"],
  ) => void;
  remove: (clientId: string) => void;
}

export const useChatOutbox = create<OutboxState>((set) => ({
  entries: [],
  add: (entry) => set((state) => ({ entries: [...state.entries, entry] })),
  setStatus: (clientId, status, rateLimited = null) =>
    set((state) => ({
      entries: state.entries.map((entry) =>
        entry.clientId === clientId ? { ...entry, status, rateLimited } : entry,
      ),
    })),
  remove: (clientId) =>
    set((state) => ({ entries: state.entries.filter((entry) => entry.clientId !== clientId) })),
}));

/** A `client_id` the server accepts (`^[A-Za-z0-9_-]{1,64}$`). */
export function newClientId(): string {
  const random =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
  const id = `web-${random}`.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64);
  return CLIENT_ID_PATTERN.test(id) ? id : `web-${Date.now().toString(36)}`;
}

/** Network failures, 429 and 5xx keep the row for a retry (FR-084). */
export function isRetryableSendError(error: unknown): boolean {
  if (!(error instanceof ChatApiError)) return true;
  return error.status === 429 || error.status >= 500;
}

function emptyPage(message: ChatMessage): MessagePageResponse {
  return {
    items: [message],
    has_older: false,
    has_more: false,
    next_after: null,
    next_before: null,
  };
}

function mergeIntoPage(
  page: MessagePageResponse | undefined,
  message: ChatMessage,
): MessagePageResponse {
  if (page === undefined) return emptyPage(message);
  if (page.items.some((item) => item.id === message.id)) return page;
  const items = [...page.items, message].sort(
    (a, b) => a.created_at - b.created_at || a.id.localeCompare(b.id),
  );
  return { ...page, items };
}

function storeSent(queryClient: QueryClient, message: ChatMessage): void {
  const key =
    message.thread_root_id === null
      ? teamChatQueryKeys.messages(message.channel_id)
      : teamChatQueryKeys.thread(message.channel_id, message.thread_root_id);
  // Seed the page when the first fetch has not landed. Invalidating instead
  // would drop the optimistic row and let that older empty fetch win (AC-021a).
  queryClient.setQueryData<MessagePageResponse>(key, (page) => mergeIntoPage(page, message));
  if (message.thread_root_id === null) return;
  // The reply count reads the change set; the frame for this id dedupes later.
  useTeamChatStore.getState().mergeChanges([
    {
      id: message.id,
      kind: "message",
      channel_id: message.channel_id,
      thread_root_id: message.thread_root_id,
      created_at: message.created_at,
      author_id: message.author_id,
    },
  ]);
}

/** A first message in a DM the sidebar does not list yet refetches bootstrap (AC-021a). */
function refreshSidebarIfUnknown(queryClient: QueryClient, channelId: string): void {
  const bootstrap = queryClient.getQueryData<BootstrapResponse>(teamChatQueryKeys.bootstrap);
  if (bootstrap === undefined) return;
  const known =
    bootstrap.channels.some((channel) => channel.id === channelId) ||
    bootstrap.dms.some((channel) => channel.id === channelId);
  if (!known) {
    void fetchLatest(queryClient, teamChatQueryKeys.bootstrap, (signal) => fetchBootstrap(signal));
  }
}

async function deliver(queryClient: QueryClient, entry: OutboxEntry): Promise<void> {
  const outbox = useChatOutbox.getState();
  outbox.setStatus(entry.clientId, "sending");
  try {
    const message = await postMessage(entry.channelId, {
      body: entry.body,
      client_id: entry.clientId,
      thread_root_id: entry.threadRootId,
    });
    const key =
      message.thread_root_id === null
        ? teamChatQueryKeys.messages(message.channel_id)
        : teamChatQueryKeys.thread(message.channel_id, message.thread_root_id);
    // An older page fetch must not land after this write and hide the row.
    await queryClient.cancelQueries({ queryKey: key, exact: true });
    storeSent(queryClient, message);
    useChatOutbox.getState().remove(entry.clientId);
    refreshSidebarIfUnknown(queryClient, entry.channelId);
  } catch (error: unknown) {
    // FR-084 names network, 429 and 5xx (`isRetryableSendError`). Other answers
    // (403, 409) also stay as "nicht gesendet", so the text is never dropped silently.
    // A 429 stored nothing (AC-017a); the row says so and names Retry-After.
    const rateLimited =
      error instanceof ChatApiError && error.status === 429
        ? { retryAfterSeconds: error.retryAfterSeconds }
        : null;
    useChatOutbox.getState().setStatus(entry.clientId, "failed", rateLimited);
  }
}

/**
 * Show the message at once and post it. Returns false for a blank body, which
 * is never posted (AC-015a: the server would answer 422 `empty_body`).
 */
export function sendChatMessage(
  queryClient: QueryClient,
  input: { channelId: string; threadRootId: string | null; authorId: string; body: string },
): boolean {
  const body = input.body.trim();
  if (body === "") return false;
  const entry: OutboxEntry = {
    clientId: newClientId(),
    channelId: input.channelId,
    threadRootId: input.threadRootId,
    authorId: input.authorId,
    body,
    createdAtMs: Date.now(),
    status: "sending",
  };
  useChatOutbox.getState().add(entry);
  void deliver(queryClient, entry);
  return true;
}

/** "erneut senden": the same `client_id` and body. A row already sending is left alone. */
export function retryChatMessage(queryClient: QueryClient, clientId: string): void {
  const entry = useChatOutbox.getState().entries.find((item) => item.clientId === clientId);
  if (entry === undefined || entry.status === "sending") return;
  void deliver(queryClient, entry);
}
