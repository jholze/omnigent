/**
 * Omnigent HTTP client and MCP tool implementations.
 *
 * User chat is POST /v1/sessions/{id}/events with type "message". That route
 * is omitted from openapi.json; /comments is for review threads, not turns.
 * Turn completion is read from GET /v1/sessions/{id}/stream (session.status).
 */

const dispatchHttp = globalThis.fetch.bind(globalThis);

const HARD_TERMINAL = new Set([
  "response.failed",
  "response.cancelled",
  "turn.failed",
  "turn.cancelled",
]);

const TOOLS = [
  {
    name: "health",
    description: "Liveness probe. GET /health. Returns {status: ok} when the server is up.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "info",
    description:
      "Runtime capabilities. GET /v1/info. Auth mode, server version, single-user flag, sandbox providers. Unauthenticated.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "list_agents",
    description: "List agents. GET /v1/agents. scope=user lists the caller's own agents.",
    inputSchema: {
      type: "object",
      properties: {
        limit: { type: "integer", minimum: 1, maximum: 1000 },
        after: { type: "string", description: "Cursor. Return agents after this id." },
        scope: { type: "string", enum: ["user"], description: "Set to user for the caller's agents." },
      },
    },
  },
  {
    name: "list_hosts",
    description: "List hosts owned by the caller, online and offline. GET /v1/hosts.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "list_harnesses",
    description: "List installed harnesses. GET /v1/harnesses.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "list_sessions",
    description:
      "List sessions. GET /v1/sessions. visibility is always sent (all, mine, shared, or archived; default all).",
    inputSchema: {
      type: "object",
      properties: {
        limit: { type: "integer", minimum: 1, maximum: 1000 },
        after: { type: "string" },
        before: { type: "string" },
        agent_id: { type: "string" },
        agent_name: { type: "string" },
        search_query: { type: "string", description: "Case-insensitive match on title or conversation text." },
        include_archived: { type: "boolean" },
        order: { type: "string", enum: ["asc", "desc"] },
        sort_by: { type: "string", enum: ["created_at", "updated_at"] },
        project: { type: "string" },
        kind: { type: "string", enum: ["default", "sub_agent", "any"] },
        visibility: { type: "string", enum: ["all", "mine", "shared", "archived"] },
      },
    },
  },
  {
    name: "get_session",
    description:
      "Fetch one session. GET /v1/sessions/{session_id}. The embedded items array is omitted; use get_session_items for the transcript.",
    inputSchema: {
      type: "object",
      properties: { session_id: { type: "string" } },
      required: ["session_id"],
    },
  },
  {
    name: "get_session_items",
    description:
      "Conversation transcript. GET /v1/sessions/{session_id}/items. Default page is the newest 30 items (order=desc). Text is truncated.",
    inputSchema: {
      type: "object",
      properties: {
        session_id: { type: "string" },
        limit: { type: "integer", minimum: 1, maximum: 200, description: "Default 30." },
        after: { type: "string" },
        before: { type: "string" },
        order: { type: "string", enum: ["asc", "desc"], description: "desc (default) is newest first. asc is chronological." },
        max_chars: { type: "integer", minimum: 200, maximum: 20000, description: "Max characters kept per text block. Default 2000." },
      },
      required: ["session_id"],
    },
  },
  {
    name: "create_session",
    description:
      "Create a session bound to an agent. POST /v1/sessions. Dry-run unless confirm is true: returns method, path, and body and does not call the server. " +
      "Optional message is stored as initial_items. To wait for the reply, create without a message, then call send_message with wait.",
    inputSchema: {
      type: "object",
      properties: {
        agent_id: { type: "string", description: "Durable agent id, for example ag_abc123." },
        host_id: { type: "string", description: "External host to launch on. Required with workspace when the server should start the runner." },
        workspace: {
          type: "string",
          description: "Absolute path on an external host, or a git URL (optional #branch) when host_type is managed.",
        },
        host_type: { type: "string", enum: ["external", "managed"] },
        title: { type: "string" },
        message: { type: "string", description: "Optional first user message." },
        confirm: { type: "boolean", description: "Perform the POST. Default false returns the request and sends nothing." },
      },
      required: ["agent_id"],
    },
  },
  {
    name: "send_message",
    description:
      "Post a user message into a session. POST /v1/sessions/{session_id}/events with type message and an input_text block. " +
      "Dry-run unless confirm is true. wait opens GET /v1/sessions/{id}/stream?idle=false before the POST and returns the assistant reply when session.status settles to idle or failed, when an elicitation arrives, or at timeout_seconds.",
    inputSchema: {
      type: "object",
      properties: {
        session_id: { type: "string" },
        text: { type: "string" },
        wait: { type: "boolean", description: "Stream until the turn ends and return the assistant reply. Default false." },
        timeout_seconds: { type: "number", description: "Wait budget in seconds. Default 120, max 600." },
        confirm: { type: "boolean", description: "Perform the POST. Default false returns the request and sends nothing." },
      },
      required: ["session_id", "text"],
    },
  },
  {
    name: "interrupt_turn",
    description:
      "Cancel the in-flight turn without deleting the session. POST /v1/sessions/{session_id}/events {type: interrupt}. Dry-run unless confirm is true.",
    inputSchema: {
      type: "object",
      properties: {
        session_id: { type: "string" },
        confirm: { type: "boolean", description: "Perform the POST. Default false returns the request and sends nothing." },
      },
      required: ["session_id"],
    },
  },
  {
    name: "stop_session",
    description:
      "Stop the live session process without deleting the conversation. POST /v1/sessions/{session_id}/events {type: stop_session}. Owner-only on the server. The next message can relaunch it. Dry-run unless confirm is true.",
    inputSchema: {
      type: "object",
      properties: {
        session_id: { type: "string" },
        confirm: { type: "boolean", description: "Perform the POST. Default false returns the request and sends nothing." },
      },
      required: ["session_id"],
    },
  },
];

export function toolDefinitions() {
  return TOOLS;
}

export function loadConfig(env = process.env) {
  const raw = env.OMNIGENT_URL || "http://127.0.0.1:6767";
  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new Error(`OMNIGENT_URL is not a valid URL: ${raw}`);
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("OMNIGENT_URL must be http or https");
  }
  const base = url.origin + url.pathname.replace(/\/+$/, "");
  const headers = { accept: "application/json" };
  if (env.OMNIGENT_EMAIL) {
    headers[env.OMNIGENT_AUTH_HEADER || "X-Forwarded-Email"] = env.OMNIGENT_EMAIL;
  }
  return { base, headers };
}

function snippet(body) {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  if (!text) return "";
  return text.length > 1500 ? `${text.slice(0, 1500)}…` : text;
}

async function callOmnigent(config, method, path, body, timeoutMs = 30_000) {
  const headers = { ...config.headers };
  if (body !== undefined) headers["content-type"] = "application/json";
  let response;
  try {
    response = await dispatchHttp(config.base + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    const timedOut = error?.name === "TimeoutError" || error?.name === "AbortError";
    throw new Error(
      timedOut
        ? `Omnigent ${method} ${path} timed out`
        : `Omnigent ${method} ${path} failed: ${error?.message || error}`,
    );
  }
  const text = await response.text();
  let parsed = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }
  if (!response.ok) {
    throw new Error(`Omnigent ${method} ${path} returned HTTP ${response.status}: ${snippet(parsed)}`);
  }
  return parsed;
}

function withQuery(path, params) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    query.set(key, String(value));
  }
  const text = query.toString();
  return text ? `${path}?${text}` : path;
}

function requireId(value, name) {
  if (typeof value !== "string" || value.length === 0) throw new Error(`${name} is required`);
  if (/[\s/?#]/.test(value)) throw new Error(`${name} contains invalid characters`);
  return value;
}

function optionalString(value, name) {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "string") throw new Error(`${name} must be a string`);
  return value;
}

function requireText(value) {
  if (typeof value !== "string" || value.length === 0) throw new Error("text is required");
  return value;
}

function optionalInt(value, name, min, max) {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "number" || !Number.isInteger(value) || value < min || value > max) {
    throw new Error(`${name} must be an integer from ${min} to ${max}`);
  }
  return value;
}

function oneOf(value, name, allowed) {
  if (value === undefined || value === null) return undefined;
  if (!allowed.includes(value)) throw new Error(`${name} must be one of ${allowed.join(", ")}`);
  return value;
}

function timeoutMs(value) {
  if (value === undefined || value === null) return 120_000;
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0 || value > 600) {
    throw new Error("timeout_seconds must be a number in (0, 600]");
  }
  return Math.round(value * 1000);
}

function userMessageEvent(text) {
  return {
    type: "message",
    data: {
      role: "user",
      content: [{ type: "input_text", text }],
    },
  };
}

function preview(method, path, body, extra = {}) {
  return { dry_run: true, method, path, body, ...extra };
}

function presentSession(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return payload;
  if (!Array.isArray(payload.items)) return payload;
  const { items, ...rest } = payload;
  return { ...rest, items_omitted: true, item_count: items.length };
}

function clip(text, maxChars) {
  if (text.length <= maxChars) return text;
  return `${text.slice(0, maxChars)}…[truncated]`;
}

function truncateBlock(block, maxChars) {
  if (!block || typeof block !== "object" || typeof block.text !== "string") return block;
  if (block.text.length <= maxChars) return block;
  return { ...block, text: clip(block.text, maxChars), truncated: true };
}

function truncateItem(item, maxChars) {
  if (!item || typeof item !== "object") return item;
  const copy = { ...item };
  if (Array.isArray(copy.content)) copy.content = copy.content.map((block) => truncateBlock(block, maxChars));
  if (copy.data && typeof copy.data === "object" && !Array.isArray(copy.data)) {
    copy.data = { ...copy.data };
    if (Array.isArray(copy.data.content)) {
      copy.data.content = copy.data.content.map((block) => truncateBlock(block, maxChars));
    }
  }
  for (const key of ["arguments", "output"]) {
    if (typeof copy[key] === "string") copy[key] = clip(copy[key], maxChars);
  }
  return copy;
}

function presentItems(payload, maxChars) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return payload;
  return {
    object: payload.object ?? "list",
    data: Array.isArray(payload.data) ? payload.data.map((item) => truncateItem(item, maxChars)) : payload.data,
    first_id: payload.first_id ?? null,
    last_id: payload.last_id ?? null,
    has_more: Boolean(payload.has_more),
  };
}

function textFromContent(content) {
  if (!Array.isArray(content)) return "";
  return content
    .filter((block) => block && typeof block.text === "string")
    .map((block) => block.text)
    .join("");
}

function replyAfterUser(page, userText) {
  const data = Array.isArray(page?.data) ? page.data : [];
  let seen = false;
  let reply = "";
  for (const item of [...data].reverse()) {
    if (!item || item.type !== "message") continue;
    const role = item.role ?? item.data?.role;
    const text = textFromContent(item.content ?? item.data?.content);
    if (role === "user" && text === userText) {
      seen = true;
      reply = "";
      continue;
    }
    if (seen && role === "assistant" && text) reply = text;
  }
  return seen ? reply : "";
}

function flattenEvent(event) {
  if (!event || typeof event !== "object") return event;
  const data = event.data;
  if (!data || typeof data !== "object" || Array.isArray(data)) return event;
  return { ...data, ...event, type: event.type || data.type };
}

function statusOf(event) {
  if (event.type !== "session.status" || typeof event.status !== "string") return null;
  const responseId = typeof event.response_id === "string" && event.response_id ? event.response_id : null;
  return { status: event.status, responseId };
}

class TurnState {
  constructor() {
    this.turnStarted = false;
    this.turnProduced = false;
    this.openResponseId = null;
    this.sawOpenRunning = false;
    this.buckets = new Map();
    this.lastIndex = new Map();
    this.status = null;
    this.done = false;
    this.endReason = null;
    this.needsInput = null;
    this.error = null;
    this.eventCount = 0;
  }

  replyText() {
    return [...this.buckets.values()].join("");
  }

  finish(reason) {
    if (this.done) return;
    this.done = true;
    this.endReason = reason;
  }

  addDelta(event) {
    const id = typeof event.message_id === "string" ? event.message_id : "";
    const delta = typeof event.delta === "string" ? event.delta : "";
    if (!delta) return;
    if (typeof event.index === "number") {
      const previous = this.lastIndex.get(id);
      if (previous !== undefined && event.index <= previous) return;
      this.lastIndex.set(id, event.index);
    }
    const soFar = this.buckets.get(id) || "";
    this.buckets.set(id, soFar && delta.startsWith(soFar) ? delta : soFar + delta);
  }

  captureCompleted(event) {
    if (this.replyText()) return;
    const output = event.response?.output;
    if (!Array.isArray(output)) return;
    const text = output.map((item) => textFromContent(item?.content)).join("");
    if (text) this.buckets.set("", text);
  }

  observe(raw) {
    if (this.done) return;
    const event = flattenEvent(raw);
    if (!event || typeof event.type !== "string") return;
    this.eventCount += 1;
    if (event.type === "[DONE]") {
      if (this.turnStarted) this.finish("done");
      return;
    }
    if (event.type === "response.output_text.delta") {
      this.addDelta(event);
      this.turnStarted = true;
      this.turnProduced = true;
    } else if (event.type === "response.completed" || event.type === "response.incomplete") {
      this.turnProduced = true;
      this.captureCompleted(event);
    } else if (
      event.type === "session.input.consumed" ||
      event.type === "response.created" ||
      event.type === "response.in_progress"
    ) {
      this.turnStarted = true;
    } else if (event.type === "response.elicitation_request") {
      this.turnStarted = true;
      this.status = this.status || "waiting";
      this.needsInput = {
        elicitation_id: event.elicitation_id ?? null,
        message: event.params?.message ?? event.message ?? null,
      };
      this.finish("elicitation");
      return;
    }
    if (HARD_TERMINAL.has(event.type)) {
      const detail = event.response?.error ?? event.error;
      this.error =
        typeof detail === "string" ? detail : detail?.message || event.type;
      if (event.type === "response.failed" || event.type === "turn.failed") this.status = "failed";
      if (event.type === "response.cancelled" || event.type === "turn.cancelled") this.status = "idle";
      this.finish(event.type);
      return;
    }
    const parsed = statusOf(event);
    if (!parsed) return;
    const { status, responseId } = parsed;
    if (status === "running" || status === "waiting" || status === "launching") {
      this.turnStarted = true;
      this.status = status;
      if (responseId) {
        this.openResponseId = responseId;
        this.sawOpenRunning = true;
      }
      return;
    }
    if (status !== "idle" && status !== "failed") return;
    // A connect-time replay of the previous idle, and a native PTY lull
    // before the first token, are not the end of this turn.
    if (!this.turnStarted) return;
    const idMatch = Boolean(responseId) && (!this.sawOpenRunning || responseId === this.openResponseId);
    const idLessEnd = !responseId && !this.sawOpenRunning && (this.turnProduced || status === "failed");
    if (idMatch || idLessEnd) {
      this.status = status;
      if (status === "failed") this.error = event.error ?? this.error;
      this.finish(status);
    }
  }

  toResult(accepted, timedOut) {
    return {
      accepted,
      status: this.status,
      reply: this.replyText(),
      reply_source: "stream",
      timed_out: timedOut,
      needs_input: this.needsInput,
      error: this.error,
      end_reason: timedOut ? "timeout" : this.endReason,
      event_count: this.eventCount,
    };
  }
}

function createSseParser() {
  let buf = "";
  const parseBlock = (raw) => {
    let eventName = null;
    const dataLines = [];
    for (const line of raw.split("\n")) {
      if (!line || line.startsWith(":")) continue;
      const split = line.indexOf(":");
      const field = split === -1 ? line : line.slice(0, split);
      let value = split === -1 ? "" : line.slice(split + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") eventName = value;
      else if (field === "data") dataLines.push(value);
    }
    if (dataLines.length === 0) return null;
    const data = dataLines.join("\n");
    if (data === "[DONE]") return { type: "[DONE]" };
    try {
      const parsed = JSON.parse(data);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
      if (!parsed.type && eventName) parsed.type = eventName;
      return parsed;
    } catch {
      return null;
    }
  };
  const take = (flush) => {
    const events = [];
    buf = buf.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    let idx = buf.indexOf("\n\n");
    while (idx !== -1) {
      const event = parseBlock(buf.slice(0, idx));
      buf = buf.slice(idx + 2);
      if (event) events.push(event);
      idx = buf.indexOf("\n\n");
    }
    if (flush && buf.trim()) {
      const event = parseBlock(buf);
      buf = "";
      if (event) events.push(event);
    }
    return events;
  };
  return {
    push(chunk) {
      buf += chunk;
      return take(false);
    },
    finish() {
      return take(true);
    },
  };
}

async function watchTurn(config, sessionId, body, budgetMs) {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, budgetMs);
  const streamPath = `/v1/sessions/${encodeURIComponent(sessionId)}/stream?idle=false`;
  let response;
  try {
    response = await dispatchHttp(config.base + streamPath, {
      method: "GET",
      headers: { ...config.headers, accept: "text/event-stream" },
      signal: controller.signal,
    });
  } catch (error) {
    clearTimeout(timer);
    if (timedOut) throw new Error(`timed out opening session stream after ${budgetMs}ms`);
    throw new Error(`Omnigent GET ${streamPath} failed: ${error?.message || error}`);
  }
  if (!response.ok) {
    clearTimeout(timer);
    const text = await response.text();
    throw new Error(`Omnigent GET ${streamPath} returned HTTP ${response.status}: ${snippet(text)}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = createSseParser();
  const state = new TurnState();
  let accepted;
  let postError;
  const posted = callOmnigent(config, "POST", `/v1/sessions/${encodeURIComponent(sessionId)}/events`, body).then(
    (value) => {
      accepted = value;
    },
    (error) => {
      postError = error;
      controller.abort();
    },
  );
  try {
    while (!state.done) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const event of parser.push(decoder.decode(value, { stream: true }))) {
        state.observe(event);
        if (state.done) break;
      }
    }
    if (!state.done) {
      for (const event of parser.finish()) state.observe(event);
    }
    if (!state.done) state.endReason = state.endReason || "stream_closed";
  } catch (error) {
    if (!state.done && !timedOut && !postError) throw error;
  } finally {
    clearTimeout(timer);
    controller.abort();
    try {
      await reader.cancel();
    } catch {
      // The stream is already closed when the turn ends or the server hangs up.
    }
  }
  await posted.catch(() => {});
  if (postError) throw postError;
  return state.toResult(accepted, timedOut && !state.done);
}

function buildCreateBody(args) {
  const agentId = requireId(args.agent_id, "agent_id");
  const hostType = oneOf(args.host_type, "host_type", ["external", "managed"]);
  const hostId = optionalString(args.host_id, "host_id");
  const workspace = optionalString(args.workspace, "workspace");
  const title = optionalString(args.title, "title");
  const message = args.message === undefined || args.message === null ? undefined : requireText(args.message);
  if (hostType === "managed" && hostId) {
    throw new Error("host_id must be omitted when host_type is managed");
  }
  if (hostId && !workspace) throw new Error("workspace is required when host_id is set");
  const body = { agent_id: agentId };
  if (hostType) body.host_type = hostType;
  if (hostId) body.host_id = hostId;
  if (workspace) body.workspace = workspace;
  if (title) body.title = title;
  if (message) body.initial_items = [userMessageEvent(message)];
  return body;
}

async function sendMessage(config, args) {
  const sessionId = requireId(args.session_id, "session_id");
  const text = requireText(args.text);
  const body = userMessageEvent(text);
  const path = `/v1/sessions/${encodeURIComponent(sessionId)}/events`;
  const wait = args.wait === true;
  const budget = timeoutMs(args.timeout_seconds);
  if (args.confirm !== true) {
    const extra = wait
      ? {
          wait: {
            stream: `GET /v1/sessions/${encodeURIComponent(sessionId)}/stream?idle=false`,
            timeout_seconds: budget / 1000,
          },
        }
      : {};
    return preview("POST", path, body, extra);
  }
  if (!wait) return callOmnigent(config, "POST", path, body);
  const result = await watchTurn(config, sessionId, body, budget);
  if (!result.reply && !result.needs_input) {
    try {
      const page = await callOmnigent(
        config,
        "GET",
        withQuery(`/v1/sessions/${encodeURIComponent(sessionId)}/items`, { limit: 30, order: "desc" }),
      );
      const recovered = replyAfterUser(page, text);
      if (recovered) {
        result.reply = recovered;
        result.reply_source = "items";
      }
    } catch {
      // Stream result stands when the transcript cannot be read.
    }
  }
  return result;
}

function control(sessionId, type, confirm) {
  const id = requireId(sessionId, "session_id");
  const path = `/v1/sessions/${encodeURIComponent(id)}/events`;
  const body = { type, data: {} };
  return { path, body, confirm: confirm === true };
}

export async function callTool(name, args, config) {
  switch (name) {
    case "health":
      return callOmnigent(config, "GET", "/health");
    case "info":
      return callOmnigent(config, "GET", "/v1/info");
    case "list_agents":
      return callOmnigent(
        config,
        "GET",
        withQuery("/v1/agents", {
          limit: optionalInt(args.limit, "limit", 1, 1000),
          after: optionalString(args.after, "after"),
          scope: oneOf(args.scope, "scope", ["user"]),
        }),
      );
    case "list_hosts":
      return callOmnigent(config, "GET", "/v1/hosts");
    case "list_harnesses":
      return callOmnigent(config, "GET", "/v1/harnesses");
    case "list_sessions": {
      const visibility = oneOf(args.visibility, "visibility", ["all", "mine", "shared", "archived"]) ?? "all";
      return callOmnigent(
        config,
        "GET",
        withQuery("/v1/sessions", {
          limit: optionalInt(args.limit, "limit", 1, 1000),
          after: optionalString(args.after, "after"),
          before: optionalString(args.before, "before"),
          agent_id: optionalString(args.agent_id, "agent_id"),
          agent_name: optionalString(args.agent_name, "agent_name"),
          search_query: optionalString(args.search_query, "search_query"),
          include_archived: args.include_archived === undefined ? undefined : Boolean(args.include_archived),
          order: oneOf(args.order, "order", ["asc", "desc"]),
          sort_by: oneOf(args.sort_by, "sort_by", ["created_at", "updated_at"]),
          project: optionalString(args.project, "project"),
          kind: oneOf(args.kind, "kind", ["default", "sub_agent", "any"]),
          visibility,
        }),
      );
    }
    case "get_session":
      return presentSession(
        await callOmnigent(config, "GET", `/v1/sessions/${encodeURIComponent(requireId(args.session_id, "session_id"))}`),
      );
    case "get_session_items": {
      const sessionId = requireId(args.session_id, "session_id");
      const maxChars = optionalInt(args.max_chars, "max_chars", 200, 20_000) ?? 2000;
      const page = await callOmnigent(
        config,
        "GET",
        withQuery(`/v1/sessions/${encodeURIComponent(sessionId)}/items`, {
          limit: optionalInt(args.limit, "limit", 1, 200) ?? 30,
          after: optionalString(args.after, "after"),
          before: optionalString(args.before, "before"),
          order: oneOf(args.order, "order", ["asc", "desc"]) ?? "desc",
        }),
      );
      return presentItems(page, maxChars);
    }
    case "create_session": {
      const body = buildCreateBody(args);
      if (args.confirm !== true) return preview("POST", "/v1/sessions", body);
      return { session: presentSession(await callOmnigent(config, "POST", "/v1/sessions", body)) };
    }
    case "send_message":
      return sendMessage(config, args);
    case "interrupt_turn": {
      const planned = control(args.session_id, "interrupt", args.confirm);
      if (!planned.confirm) return preview("POST", planned.path, planned.body);
      return callOmnigent(config, "POST", planned.path, planned.body);
    }
    case "stop_session": {
      const planned = control(args.session_id, "stop_session", args.confirm);
      if (!planned.confirm) return preview("POST", planned.path, planned.body);
      return callOmnigent(config, "POST", planned.path, planned.body);
    }
    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}
