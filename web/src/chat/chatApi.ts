/**
 * REST client for `/v1/chat/*`.
 *
 * Message bodies are returned to the query cache and never logged.
 */
import { authenticatedFetch } from "@/lib/identity";

export class ChatApiError extends Error {
  readonly status: number;
  /** Whole seconds from a `Retry-After` header (429), else null. */
  readonly retryAfterSeconds: number | null;

  constructor(status: number, retryAfterSeconds: number | null = null) {
    super(`chat request failed (${status})`);
    this.name = "ChatApiError";
    this.status = status;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

/** `Retry-After` as whole seconds. Delta-seconds and HTTP dates are accepted. */
export function parseRetryAfter(value: string | null, nowMs = Date.now()): number | null {
  if (value === null) return null;
  const trimmed = value.trim();
  if (trimmed === "") return null;
  if (/^\d+$/.test(trimmed)) return Math.max(1, Number.parseInt(trimmed, 10));
  const at = Date.parse(trimmed);
  if (Number.isNaN(at)) return null;
  return Math.max(1, Math.ceil((at - nowMs) / 1000));
}

function apiError(response: Response): ChatApiError {
  const retryAfter =
    response.status === 429 ? parseRetryAfter(response.headers.get("Retry-After")) : null;
  return new ChatApiError(response.status, retryAfter);
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
  /** Owning human of an agent account. */
  owner_id?: string | null;
}

/** One row of `GET /v1/chat/channels/{id}/members`. */
export interface ChannelMember {
  principal_id: string;
  /** `human` or `agent_account`. */
  kind: string;
  display: string;
  /**
   * Agent presence from the members route (`aktiv` or `inaktiv`).
   * Humans omit it. Kept so the mention row can show it.
   */
  status?: string | null;
}

/** One owned agent account from `GET /v1/chat/agent-accounts`. */
export interface AgentAccountRecord {
  id: string;
  principal_id: string;
  owner_id: string;
  display_name: string;
  handle: string;
  token_prefix: string;
  secret_version: number;
  last_seen_at: number | null;
}

/** Create or rotate response. The token and secret are shown once. */
export interface AgentAccountIssued {
  account: AgentAccountRecord;
  token: string;
  webhook_secret: string;
}

const AGENT_ACTIVE_WINDOW_MS = 10 * 60 * 1000;

/**
 * `aktiv` when `last_seen_at` falls inside the last 10 minutes.
 * Accepts epoch microseconds, milliseconds, or seconds.
 */
export function agentAccountActivity(
  lastSeenAt: number | null | undefined,
  nowMs = Date.now(),
): "aktiv" | "inaktiv" {
  if (lastSeenAt == null) return "inaktiv";
  const seenMs =
    lastSeenAt > 1e14
      ? Math.floor(lastSeenAt / 1000)
      : lastSeenAt < 1e11
        ? lastSeenAt * 1000
        : lastSeenAt;
  return nowMs - seenMs <= AGENT_ACTIVE_WINDOW_MS ? "aktiv" : "inaktiv";
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
  /**
   * `chat_threads.agent_state` on a thread root (`thread_root_id` null), when
   * the thread has a row. Replies omit it. Read-only.
   */
  agent_state?: string | null;
  /**
   * `chat_threads.session_id` on a thread root, when the agent thread has a
   * session. Lets a reloaded client mount the live view. Replies omit it.
   * Read-only.
   */
  session_id?: string | null;
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
  /** Active agent threads in one channel, including roots outside the tail page. */
  agentThreads: (channelId: string) => ["team-chat", "agent-threads", channelId] as const,
  /** Replies of one thread. Separate prefix, so a channel refetch never touches it. */
  thread: (channelId: string, threadRootId: string) =>
    ["team-chat", "thread", channelId, threadRootId] as const,
  members: (channelId: string) => ["team-chat", "members", channelId] as const,
  /** Lines of the session chat on one root session (FR-029). */
  sessionChat: (sessionId: string) => ["team-chat", "session-chat", sessionId] as const,
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
    (value.client_id === null || typeof value.client_id === "string") &&
    (value.agent_state == null || typeof value.agent_state === "string") &&
    (value.session_id == null || typeof value.session_id === "string")
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
  if (!response.ok) throw apiError(response);
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

/** Active agent thread in a channel. `GET /v1/chat/channels/{id}/agent-threads`. */
export interface ChannelAgentThread {
  root_message_id: string;
  agent_state: string;
  /** Present once the agent session exists. Same id as `thread.updated`. */
  session_id?: string | null;
  /** Root message text. Omitted when that row is missing; the strip then says "Agent-Thread". */
  body?: string | null;
}

function isChannelAgentThread(value: unknown): value is ChannelAgentThread {
  if (!isRecord(value)) return false;
  return (
    typeof value.root_message_id === "string" &&
    value.root_message_id !== "" &&
    typeof value.agent_state === "string" &&
    (value.session_id == null || typeof value.session_id === "string") &&
    (value.body == null || typeof value.body === "string")
  );
}

function isChannelAgentThreadList(value: unknown): value is ChannelAgentThread[] {
  return Array.isArray(value) && value.every(isChannelAgentThread);
}

/**
 * Active agent threads in the channel, including roots outside the loaded
 * tail of 50 messages. A non-member gets the route's 403 or 404.
 */
export async function fetchChannelAgentThreads(
  channelId: string,
  signal?: AbortSignal,
): Promise<ChannelAgentThread[]> {
  const response = await authenticatedFetch(
    `/v1/chat/channels/${encodeURIComponent(channelId)}/agent-threads`,
    { signal },
  );
  return readJson(response, "Invalid chat agent threads response", isChannelAgentThreadList);
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

function isChannelMember(value: unknown): value is ChannelMember {
  if (!isRecord(value)) return false;
  return (
    typeof value.principal_id === "string" &&
    typeof value.kind === "string" &&
    typeof value.display === "string"
  );
}

function isChannelMemberList(value: unknown): value is ChannelMember[] {
  return Array.isArray(value) && value.every(isChannelMember);
}

/** Members of one channel, for mention autocomplete. */
export async function fetchChannelMembers(
  channelId: string,
  signal?: AbortSignal,
): Promise<ChannelMember[]> {
  const response = await authenticatedFetch(
    `/v1/chat/channels/${encodeURIComponent(channelId)}/members`,
    { signal },
  );
  return readJson(response, "Invalid chat members response", isChannelMemberList);
}

function isAgentAccountRecord(value: unknown): value is AgentAccountRecord {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === "string" &&
    typeof value.principal_id === "string" &&
    typeof value.owner_id === "string" &&
    typeof value.display_name === "string" &&
    typeof value.handle === "string" &&
    typeof value.token_prefix === "string" &&
    typeof value.secret_version === "number" &&
    (value.last_seen_at === null || typeof value.last_seen_at === "number")
  );
}

function isAgentAccountList(value: unknown): value is AgentAccountRecord[] {
  return Array.isArray(value) && value.every(isAgentAccountRecord);
}

function isAgentAccountIssued(value: unknown): value is AgentAccountIssued {
  if (!isRecord(value)) return false;
  return (
    isAgentAccountRecord(value.account) &&
    typeof value.token === "string" &&
    typeof value.webhook_secret === "string"
  );
}

/** Active agent accounts owned by the caller. Other owners are not listed. */
export async function fetchAgentAccounts(signal?: AbortSignal): Promise<AgentAccountRecord[]> {
  const response = await authenticatedFetch("/v1/chat/agent-accounts", { signal });
  return readJson(response, "Invalid agent accounts response", isAgentAccountList);
}

/** Create an account. The body is the display name and handle only. */
export async function createAgentAccount(input: {
  display_name: string;
  handle: string;
}): Promise<AgentAccountIssued> {
  const response = await authenticatedFetch("/v1/chat/agent-accounts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ display_name: input.display_name, handle: input.handle }),
  });
  return readJson(response, "Invalid agent account response", isAgentAccountIssued);
}

/** Rotate the token. The previous token stops working. */
export async function rotateAgentAccount(accountId: string): Promise<AgentAccountIssued> {
  const response = await authenticatedFetch(
    `/v1/chat/agent-accounts/${encodeURIComponent(accountId)}/rotate`,
    { method: "POST" },
  );
  return readJson(response, "Invalid agent account response", isAgentAccountIssued);
}

/** Revoke the account and drop it from the caller's list. */
export async function revokeAgentAccount(accountId: string): Promise<void> {
  const response = await authenticatedFetch(
    `/v1/chat/agent-accounts/${encodeURIComponent(accountId)}`,
    { method: "DELETE" },
  );
  if (!response.ok) throw apiError(response);
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

/** Channel read cursor: one stored top-level message. */
export interface ReadCursor {
  id: string;
  created_at: number;
}

/**
 * `POST /v1/chat/channels/{id}/read` for the channel cursor (FR-007).
 *
 * The server keeps the newer of the stored and the sent cursor, so a late
 * or repeated call never moves it back. The response body is not needed.
 */
export async function postReadCursor(channelId: string, cursor: ReadCursor): Promise<void> {
  const response = await authenticatedFetch(
    `/v1/chat/channels/${encodeURIComponent(channelId)}/read`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: cursor.id, created_at: cursor.created_at }),
    },
  );
  if (!response.ok) throw apiError(response);
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

/** One row of `GET /v1/chat/channels/{id}/members`. Only the id is required to name a DM. */
export interface ChatMember {
  principal_id: string;
}

function isChatMember(value: unknown): value is ChatMember {
  return isRecord(value) && typeof value.principal_id === "string" && value.principal_id !== "";
}

function isChatMemberList(value: unknown): value is ChatMember[] {
  return Array.isArray(value) && value.every(isChatMember);
}

/** Members the caller may already read. A non-member gets the route's 403 or 404. */
export async function listChannelMembers(
  channelId: string,
  signal?: AbortSignal,
): Promise<ChatMember[]> {
  const response = await authenticatedFetch(
    `/v1/chat/channels/${encodeURIComponent(channelId)}/members`,
    { signal },
  );
  return readJson(response, "Invalid chat members response", isChatMemberList);
}

/** One line of `GET /v1/sessions/{id}/chat`. `created_at` is epoch microseconds. */
export interface SessionChatLine {
  id: string;
  created_by: string | null;
  created_at: number;
  body: string;
}

/** `GET /v1/sessions/{id}/chat`: lines ascending, and whether another person has a grant. */
export interface SessionChatList {
  items: SessionChatLine[];
  shared: boolean;
}

function isSessionChatLine(value: unknown): value is SessionChatLine {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === "string" &&
    (value.created_by === null || typeof value.created_by === "string") &&
    typeof value.created_at === "number" &&
    typeof value.body === "string"
  );
}

function isSessionChatList(value: unknown): value is SessionChatList {
  if (!isRecord(value)) return false;
  return (
    typeof value.shared === "boolean" &&
    Array.isArray(value.items) &&
    value.items.every(isSessionChatLine)
  );
}

/**
 * Session chat of a root session (FR-029). The server answers 404 for an
 * Agent-Thread session (AC-029c) and 403 for a public-link visitor (AC-029b).
 */
export async function fetchSessionChat(
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionChatList> {
  const response = await authenticatedFetch(`/v1/sessions/${encodeURIComponent(sessionId)}/chat`, {
    signal,
  });
  return readJson(response, "Invalid session chat response", isSessionChatList);
}

/** `POST /v1/sessions/{id}/chat`. The server trims the body and refuses admins (403). */
export async function postSessionChat(sessionId: string, body: string): Promise<SessionChatLine> {
  const response = await authenticatedFetch(`/v1/sessions/${encodeURIComponent(sessionId)}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body }),
  });
  return readJson(response, "Invalid session chat line", isSessionChatLine);
}
