/**
 * REST client for `/v1/chat/*`.
 *
 * Message bodies are returned to the query cache and never logged.
 */
import { authenticatedFetch } from "@/lib/identity";

export class ChatApiError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`chat request failed (${status})`);
    this.name = "ChatApiError";
    this.status = status;
  }
}

export interface ChatChannel {
  id: string;
  kind: string;
  name: string | null;
  topic: string | null;
  project_name: string | null;
  visibility: string;
  created_by: string | null;
  created_at: number;
  unread_count: number;
  mention_count: number;
}

export interface DirectoryEntry {
  id: string;
  kind: string;
  display_name?: string | null;
  handle?: string | null;
}

/** `GET /v1/chat/bootstrap`. `agents_hint` is not on this server build. */
export interface BootstrapResponse {
  me: string;
  channels: ChatChannel[];
  dms: ChatChannel[];
  directory: DirectoryEntry[];
  changes_cursor: string;
}

export interface ChangeItem {
  kind: string;
  id: string;
  channel_id: string;
  thread_root_id: string | null;
  created_at: number;
}

/** `GET /v1/chat/changes`. This server's items are messages (`kind: "message"`). */
export interface ChangesResponse {
  items: ChangeItem[];
  next: string | null;
  reset: boolean;
}

export interface ChatMessage {
  id: string;
  channel_id: string;
  thread_root_id: string | null;
  author_id: string;
  author_kind: string;
  created_at: number;
  body: string;
  client_id: string | null;
}

export interface MessagePageResponse {
  items: ChatMessage[];
  has_older: boolean;
  has_more: boolean;
  next_after: string | null;
  next_before: string | null;
}

export const teamChatQueryKeys = {
  bootstrap: ["team-chat", "bootstrap"] as const,
  messages: (channelId: string) => ["team-chat", "messages", channelId] as const,
  /** Replies of one thread. Separate prefix, so a channel refetch never touches it. */
  thread: (channelId: string, threadRootId: string) =>
    ["team-chat", "thread", channelId, threadRootId] as const,
};

/** Server rule for `client_id` (`POST .../messages`). */
export const CLIENT_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isBootstrapResponse(value: unknown): value is BootstrapResponse {
  if (!isRecord(value)) return false;
  return (
    typeof value.me === "string" &&
    Array.isArray(value.channels) &&
    Array.isArray(value.dms) &&
    Array.isArray(value.directory) &&
    typeof value.changes_cursor === "string"
  );
}

function isChangesResponse(value: unknown): value is ChangesResponse {
  if (!isRecord(value)) return false;
  return (
    Array.isArray(value.items) &&
    (value.next === null || typeof value.next === "string") &&
    typeof value.reset === "boolean"
  );
}

function isChatMessage(value: unknown): value is ChatMessage {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === "string" &&
    typeof value.channel_id === "string" &&
    (value.thread_root_id === null || typeof value.thread_root_id === "string") &&
    typeof value.author_id === "string" &&
    typeof value.author_kind === "string" &&
    typeof value.created_at === "number" &&
    typeof value.body === "string" &&
    (value.client_id === null || typeof value.client_id === "string")
  );
}

function isChatChannel(value: unknown): value is ChatChannel {
  if (!isRecord(value)) return false;
  return typeof value.id === "string" && value.id !== "" && typeof value.kind === "string";
}

function isMessagePageResponse(value: unknown): value is MessagePageResponse {
  if (!isRecord(value)) return false;
  return (
    Array.isArray(value.items) &&
    typeof value.has_older === "boolean" &&
    typeof value.has_more === "boolean" &&
    (value.next_after === null || typeof value.next_after === "string") &&
    (value.next_before === null || typeof value.next_before === "string")
  );
}

async function readJson<T>(
  response: Response,
  invalidMessage: string,
  accept: (value: unknown) => value is T,
): Promise<T> {
  if (!response.ok) throw new ChatApiError(response.status);
  const payload: unknown = await response.json();
  if (!accept(payload)) throw new Error(invalidMessage);
  return payload;
}

export async function fetchBootstrap(signal?: AbortSignal): Promise<BootstrapResponse> {
  const response = await authenticatedFetch("/v1/chat/bootstrap", { signal });
  return readJson(response, "Invalid chat bootstrap response", isBootstrapResponse);
}

export async function fetchChanges(after: string, signal?: AbortSignal): Promise<ChangesResponse> {
  const params = new URLSearchParams({ after });
  const response = await authenticatedFetch(`/v1/chat/changes?${params}`, { signal });
  return readJson(response, "Invalid chat changes response", isChangesResponse);
}

export async function fetchChannelMessages(
  channelId: string,
  signal?: AbortSignal,
): Promise<MessagePageResponse> {
  const params = new URLSearchParams({ tail: "true" });
  const response = await authenticatedFetch(
    `/v1/chat/channels/${encodeURIComponent(channelId)}/messages?${params}`,
    { signal },
  );
  return readJson(response, "Invalid chat messages response", isMessagePageResponse);
}

/** Newest replies of one thread, ascending (`?tail=true&thread_root_id=`). */
export async function fetchThreadMessages(
  channelId: string,
  threadRootId: string,
  signal?: AbortSignal,
): Promise<MessagePageResponse> {
  const params = new URLSearchParams({ tail: "true", thread_root_id: threadRootId });
  const response = await authenticatedFetch(
    `/v1/chat/channels/${encodeURIComponent(channelId)}/messages?${params}`,
    { signal },
  );
  return readJson(response, "Invalid chat thread response", isMessagePageResponse);
}

export interface PostMessageInput {
  body: string;
  client_id?: string;
  thread_root_id?: string | null;
}

/**
 * `POST /v1/chat/channels/{id}/messages`.
 *
 * 201 stores, 200 replays the same `client_id`. Any other status throws a
 * `ChatApiError`; a network failure throws the fetch error unchanged.
 */
export async function postMessage(
  channelId: string,
  input: PostMessageInput,
): Promise<ChatMessage> {
  const payload: Record<string, string> = { body: input.body };
  if (input.client_id) payload.client_id = input.client_id;
  if (input.thread_root_id) payload.thread_root_id = input.thread_root_id;
  const response = await authenticatedFetch(
    `/v1/chat/channels/${encodeURIComponent(channelId)}/messages`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  return readJson(response, "Invalid chat message response", isChatMessage);
}

/**
 * `POST /v1/chat/dms` with the other participants (never the caller).
 * 201 creates, 200 returns the existing DM; both carry the same channel id.
 */
export async function openDm(principals: string[]): Promise<ChatChannel> {
  const response = await authenticatedFetch("/v1/chat/dms", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ principals }),
  });
  return readJson(response, "Invalid chat DM response", isChatChannel);
}
