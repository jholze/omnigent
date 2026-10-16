import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createServer } from "node:http";
import { createInterface } from "node:readline";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { loadConfig } from "../src/omnigent.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const serverPath = path.join(root, "src", "server.js");

const TOOL_NAMES = [
  "health",
  "info",
  "list_agents",
  "list_hosts",
  "list_harnesses",
  "list_sessions",
  "get_session",
  "get_session_items",
  "create_session",
  "send_message",
  "interrupt_turn",
  "stop_session",
];

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      if (!raw) return resolve(null);
      try {
        resolve(JSON.parse(raw));
      } catch {
        resolve(raw);
      }
    });
    req.on("error", reject);
  });
}

function json(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(body) });
  res.end(body);
}

function sse(data) {
  return `event: ${data.type}\ndata: ${JSON.stringify(data)}\n\n`;
}

class Mcp {
  constructor(url, extraEnv = {}) {
    const env = { ...process.env };
    delete env.OMNIGENT_EMAIL;
    delete env.OMNIGENT_AUTH_HEADER;
    delete env.OMNIGENT_ACCESS_TOKEN;
    delete env.OMNIGENT_SESSION_COOKIE;
    env.OMNIGENT_URL = url;
    Object.assign(env, extraEnv);
    this.child = spawn(process.execPath, [serverPath], { env, stdio: ["pipe", "pipe", "pipe"] });
    this.stderr = "";
    this.child.stderr.on("data", (chunk) => {
      this.stderr += chunk;
    });
    this.queue = [];
    this.waiters = [];
    this.lines = createInterface({ input: this.child.stdout });
    this.lines.on("line", (line) => {
      if (!line.trim()) return;
      let message;
      try {
        message = JSON.parse(line);
      } catch (error) {
        const waiter = this.waiters.shift();
        const failure = new Error(`non-json stdout: ${line}\n${error}`);
        if (waiter) waiter.reject(failure);
        else this.queue.push(Promise.reject(failure));
        return;
      }
      const waiter = this.waiters.shift();
      if (waiter) waiter.resolve(message);
      else this.queue.push(message);
    });
    this.nextId = 1;
  }

  request(method, params, ms = 8000) {
    const id = this.nextId++;
    this.child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`);
    return this.next(ms);
  }

  notify(method, params = {}) {
    this.child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", method, params })}\n`);
  }

  next(ms = 8000) {
    const pending = this.queue.shift();
    if (pending) return Promise.resolve(pending);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error(`timed out waiting for MCP response\n${this.stderr}`)),
        ms,
      );
      this.waiters.push({
        resolve: (message) => {
          clearTimeout(timer);
          resolve(message);
        },
        reject: (error) => {
          clearTimeout(timer);
          reject(error);
        },
      });
    });
  }

  async close() {
    this.lines.close();
    if (this.child.exitCode !== null || this.child.signalCode) return;
    this.child.kill();
    await once(this.child, "exit");
  }
}

async function withMock(handler, fn, options = {}) {
  const httpServer = createServer((req, res) => {
    Promise.resolve(handler(req, res)).catch((error) => {
      if (!res.headersSent) json(res, 500, { error: error.message });
      else res.destroy();
    });
  });
  httpServer.listen(0, "127.0.0.1");
  await once(httpServer, "listening");
  const { port } = httpServer.address();
  const url = options.url ? options.url(port) : `http://127.0.0.1:${port}`;
  const mcp = new Mcp(url, options.env);
  try {
    await fn(mcp, { port });
  } finally {
    await mcp.close();
    httpServer.closeAllConnections?.();
    httpServer.close();
    if (httpServer.listening) await once(httpServer, "close");
  }
}

async function handshake(mcp) {
  const init = await mcp.request("initialize", {
    protocolVersion: "2024-11-05",
    capabilities: {},
    clientInfo: { name: "test", version: "0" },
  });
  assert.equal(init.id, 1);
  assert.equal(init.result.serverInfo.name, "omnigent");
  mcp.notify("notifications/initialized");
  return init;
}

async function callTool(mcp, name, args) {
  const response = await mcp.request("tools/call", { name, arguments: args ?? {} });
  assert.equal(response.error, undefined, JSON.stringify(response.error));
  assert.notEqual(response.result.isError, true, response.result.content?.[0]?.text);
  return JSON.parse(response.result.content[0].text);
}

async function callToolError(mcp, name, args) {
  const response = await mcp.request("tools/call", { name, arguments: args ?? {} });
  assert.equal(response.error, undefined);
  assert.equal(response.result.isError, true);
  return response.result.content[0].text;
}

test("initialize, tools/list, and ping", { timeout: 15000 }, async () => {
  await withMock(
    (_req, res) => json(res, 200, {}),
    async (mcp) => {
      const init = await handshake(mcp);
      assert.equal(init.result.protocolVersion, "2024-11-05");
      assert.equal(init.result.capabilities.tools.listChanged, false);
      assert.match(init.result.instructions, /confirm is true/);
      const listed = await mcp.request("tools/list", {});
      const names = listed.result.tools.map((tool) => tool.name);
      assert.deepEqual(names, TOOL_NAMES);
      for (const tool of listed.result.tools) {
        assert.equal(typeof tool.description, "string");
        assert.equal(tool.inputSchema.type, "object");
      }
      const ping = await mcp.request("ping", {});
      assert.deepEqual(ping.result, {});
      assert.equal(ping.id, 3);
    },
  );
});

test("notifications do not produce a response", { timeout: 15000 }, async () => {
  await withMock(
    (_req, res) => json(res, 200, {}),
    async (mcp) => {
      await handshake(mcp);
      mcp.notify("notifications/cancelled", { requestId: 1 });
      const ping = await mcp.request("ping");
      assert.equal(ping.id, 2);
      assert.deepEqual(ping.result, {});
    },
  );
});

test("unknown method and parse error", { timeout: 15000 }, async () => {
  await withMock(
    (_req, res) => json(res, 200, {}),
    async (mcp) => {
      const missing = await mcp.request("nope", {});
      assert.equal(missing.error.code, -32601);
      mcp.child.stdin.write("{bad\n");
      const parsed = await mcp.next();
      assert.equal(parsed.error.code, -32700);
      const ping = await mcp.request("ping");
      assert.deepEqual(ping.result, {});
    },
  );
});

test("content-length framing", { timeout: 15000 }, async () => {
  const child = spawn(process.execPath, [serverPath], {
    env: { ...process.env, OMNIGENT_URL: "http://127.0.0.1:9" },
    stdio: ["pipe", "pipe", "pipe"],
  });
  const body = JSON.stringify({
    jsonrpc: "2.0",
    id: 7,
    method: "initialize",
    params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "t", version: "0" } },
  });
  child.stdin.write(`Content-Length: ${Buffer.byteLength(body)}\r\n\r\n${body}`);
  const chunks = [];
  const response = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("timed out")), 5000);
    child.stdout.on("data", (chunk) => {
      chunks.push(chunk);
      const buf = Buffer.concat(chunks);
      const headerEnd = buf.indexOf("\r\n\r\n");
      if (headerEnd === -1) return;
      const header = buf.subarray(0, headerEnd).toString("utf8");
      const match = /Content-Length:\s*(\d+)/i.exec(header);
      if (!match) return;
      const length = Number(match[1]);
      const start = headerEnd + 4;
      if (buf.length < start + length) return;
      clearTimeout(timer);
      resolve(JSON.parse(buf.subarray(start, start + length).toString("utf8")));
    });
  });
  child.kill();
  await once(child, "exit");
  assert.equal(response.id, 7);
  assert.equal(response.result.protocolVersion, "2025-06-18");
  assert.equal(response.result.serverInfo.name, "omnigent");
});

test("read tools hit the documented paths", { timeout: 15000 }, async () => {
  const seen = [];
  await withMock(
    async (req, res) => {
      seen.push({
        method: req.method,
        url: req.url,
        email: req.headers["x-forwarded-email"],
        authorization: req.headers.authorization,
        cookie: req.headers.cookie,
      });
      if (req.url === "/health") return json(res, 200, { status: "ok" });
      if (req.url === "/v1/info") return json(res, 200, { server_version: "0.18.0", single_user: true });
      if (req.url === "/v1/agents?limit=2&scope=user") {
        return json(res, 200, { object: "list", data: [{ id: "ag_1", name: "debby" }], has_more: false });
      }
      if (req.url === "/v1/hosts") return json(res, 200, { hosts: [{ host_id: "host_1", status: "online" }] });
      if (req.url === "/v1/harnesses") return json(res, 200, { harnesses: ["claude-sdk"] });
      if (req.url.startsWith("/v1/sessions?")) {
        const query = new URL(req.url, "http://127.0.0.1").searchParams;
        assert.equal(query.get("visibility"), "mine");
        assert.equal(query.get("agent_id"), "ag_1");
        assert.equal(query.get("search_query"), "auth");
        assert.equal(query.get("include_archived"), "true");
        assert.equal(query.get("limit"), "5");
        return json(res, 200, { object: "list", data: [{ id: "conv_1" }], has_more: false });
      }
      return json(res, 404, { missing: req.url });
    },
    async (mcp) => {
      await handshake(mcp);
      assert.deepEqual(await callTool(mcp, "health"), { status: "ok" });
      assert.equal((await callTool(mcp, "info")).single_user, true);
      assert.equal((await callTool(mcp, "list_agents", { limit: 2, scope: "user" })).data[0].id, "ag_1");
      assert.equal((await callTool(mcp, "list_hosts")).hosts[0].host_id, "host_1");
      assert.deepEqual(await callTool(mcp, "list_harnesses"), { harnesses: ["claude-sdk"] });
      const sessions = await callTool(mcp, "list_sessions", {
        visibility: "mine",
        agent_id: "ag_1",
        search_query: "auth",
        include_archived: true,
        limit: 5,
      });
      assert.equal(sessions.data[0].id, "conv_1");
      assert.equal(seen[0].email, undefined);
      assert.equal(seen[0].authorization, undefined);
      assert.equal(seen[0].cookie, undefined);
    },
    { env: {} },
  );
});

test("list_sessions defaults visibility to all", { timeout: 15000 }, async () => {
  let url = "";
  await withMock(
    (req, res) => {
      url = req.url;
      json(res, 200, { object: "list", data: [], has_more: false });
    },
    async (mcp) => {
      await handshake(mcp);
      await callTool(mcp, "list_sessions");
      assert.equal(new URL(url, "http://127.0.0.1").searchParams.get("visibility"), "all");
    },
  );
});

test("get_session omits items and get_session_items truncates", { timeout: 15000 }, async () => {
  await withMock(
    (req, res) => {
      if (req.url === "/v1/sessions/conv_1") {
        return json(res, 200, {
          id: "conv_1",
          status: "idle",
          agent_id: "ag_1",
          items: [{ id: "msg_1" }, { id: "msg_2" }],
        });
      }
      if (req.url.startsWith("/v1/sessions/conv_1/items")) {
        const query = new URL(req.url, "http://127.0.0.1").searchParams;
        assert.equal(query.get("order"), "desc");
        assert.equal(query.get("limit"), "30");
        const long = "x".repeat(250);
        return json(res, 200, {
          object: "list",
          data: [
            {
              id: "msg_2",
              type: "message",
              role: "assistant",
              content: [{ type: "output_text", text: long }],
              arguments: long,
            },
          ],
          first_id: "msg_2",
          last_id: "msg_2",
          has_more: true,
        });
      }
      return json(res, 404, {});
    },
    async (mcp) => {
      await handshake(mcp);
      const session = await callTool(mcp, "get_session", { session_id: "conv_1" });
      assert.equal(session.id, "conv_1");
      assert.equal(session.item_count, 2);
      assert.equal(session.items_omitted, true);
      assert.equal(session.items, undefined);
      const items = await callTool(mcp, "get_session_items", { session_id: "conv_1", max_chars: 200 });
      assert.equal(items.has_more, true);
      assert.equal(items.data[0].content[0].text, `${"x".repeat(200)}…[truncated]`);
      assert.equal(items.data[0].content[0].truncated, true);
      assert.equal(items.data[0].arguments, `${"x".repeat(200)}…[truncated]`);
    },
  );
});

test("identity header and trailing slash on the base URL", { timeout: 15000 }, async () => {
  let forwarded = "";
  await withMock(
    (req, res) => {
      forwarded = req.headers["x-forwarded-email"] || "";
      assert.equal(req.url, "/health");
      json(res, 200, { status: "ok" });
    },
    async (mcp) => {
      await handshake(mcp);
      await callTool(mcp, "health");
      assert.equal(forwarded, "local@example.com");
    },
    { url: (port) => `http://127.0.0.1:${port}/`, env: { OMNIGENT_EMAIL: "local@example.com" } },
  );

  let custom = "";
  await withMock(
    (req, res) => {
      custom = req.headers["x-test-user"] || "";
      assert.equal(req.headers["x-forwarded-email"], undefined);
      json(res, 200, { status: "ok" });
    },
    async (mcp) => {
      await handshake(mcp);
      await callTool(mcp, "health");
      assert.equal(custom, "local@example.com");
    },
    { env: { OMNIGENT_EMAIL: "local@example.com", OMNIGENT_AUTH_HEADER: "X-Test-User" } },
  );
});

test("writes are dry-run unless confirm is true", { timeout: 15000 }, async () => {
  let hits = 0;
  await withMock(
    (req, res) => {
      hits += 1;
      json(res, 500, { unexpected: req.url });
    },
    async (mcp) => {
      await handshake(mcp);
      const created = await callTool(mcp, "create_session", {
        agent_id: "ag_1",
        host_id: "host_1",
        workspace: "/work/app",
        title: "demo",
        message: "hello",
        confirm: false,
      });
      assert.equal(created.dry_run, true);
      assert.equal(created.method, "POST");
      assert.equal(created.path, "/v1/sessions");
      assert.deepEqual(created.body, {
        agent_id: "ag_1",
        host_id: "host_1",
        workspace: "/work/app",
        title: "demo",
        initial_items: [
          { type: "message", data: { role: "user", content: [{ type: "input_text", text: "hello" }] } },
        ],
      });
      const sent = await callTool(mcp, "send_message", {
        session_id: "conv_1",
        text: "next",
        wait: true,
        timeout_seconds: 5,
      });
      assert.deepEqual(sent.body, {
        type: "message",
        data: { role: "user", content: [{ type: "input_text", text: "next" }] },
      });
      assert.equal(sent.path, "/v1/sessions/conv_1/events");
      assert.equal(sent.wait.stream, "GET /v1/sessions/conv_1/stream?idle=false");
      const asString = await callTool(mcp, "send_message", {
        session_id: "conv_1",
        text: "nope",
        confirm: "true",
      });
      assert.equal(asString.dry_run, true);
      const interrupt = await callTool(mcp, "interrupt_turn", { session_id: "conv_1" });
      assert.deepEqual(interrupt.body, { type: "interrupt", data: {} });
      const stop = await callTool(mcp, "stop_session", { session_id: "conv_1" });
      assert.deepEqual(stop.body, { type: "stop_session", data: {} });
      assert.equal(hits, 0);
    },
  );
});

test("confirmed writes post the planned body", { timeout: 15000 }, async () => {
  const seen = [];
  await withMock(
    async (req, res) => {
      const body = req.method === "GET" ? null : await readBody(req);
      seen.push({ method: req.method, url: req.url, body });
      if (req.url === "/v1/sessions" && req.method === "POST") {
        return json(res, 201, { id: "conv_9", agent_id: "ag_1", status: "idle", items: [{ id: "msg_0" }] });
      }
      if (req.url === "/v1/sessions/conv_9/events") return json(res, 202, { queued: true, item_id: "msg_1" });
      return json(res, 404, {});
    },
    async (mcp) => {
      await handshake(mcp);
      const created = await callTool(mcp, "create_session", {
        agent_id: "ag_1",
        workspace: "/work/app",
        confirm: true,
      });
      assert.equal(created.session.id, "conv_9");
      assert.equal(created.session.item_count, 1);
      assert.equal(created.session.items, undefined);
      assert.deepEqual(seen[0].body, { agent_id: "ag_1", workspace: "/work/app" });
      const sent = await callTool(mcp, "send_message", { session_id: "conv_9", text: "go", confirm: true });
      assert.deepEqual(sent, { queued: true, item_id: "msg_1" });
      await callTool(mcp, "interrupt_turn", { session_id: "conv_9", confirm: true });
      await callTool(mcp, "stop_session", { session_id: "conv_9", confirm: true });
      assert.deepEqual(seen[2].body, { type: "interrupt", data: {} });
      assert.deepEqual(seen[3].body, { type: "stop_session", data: {} });
    },
  );
});

test("create_session rejects a managed host id and a host without a workspace", { timeout: 15000 }, async () => {
  let hits = 0;
  await withMock(
    (req, res) => {
      hits += 1;
      json(res, 200, {});
    },
    async (mcp) => {
      await handshake(mcp);
      assert.match(
        await callToolError(mcp, "create_session", {
          agent_id: "ag_1",
          host_type: "managed",
          host_id: "host_1",
          confirm: true,
        }),
        /host_id must be omitted/,
      );
      assert.match(
        await callToolError(mcp, "create_session", { agent_id: "ag_1", host_id: "host_1", confirm: true }),
        /workspace is required/,
      );
      assert.equal(hits, 0);
    },
  );
});

test("send_message wait ignores a stale idle and a response-id-less flap", { timeout: 15000 }, async () => {
  const seen = [];
  let streamRes;
  await withMock(
    async (req, res) => {
      seen.push({ method: req.method, url: req.url });
      if (req.method === "GET" && req.url.includes("/stream")) {
        streamRes = res;
        res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
        res.write(sse({ type: "session.status", status: "idle" }));
        return;
      }
      if (req.method === "POST") {
        seen.at(-1).body = await readBody(req);
        json(res, 202, { queued: true, item_id: "msg_1" });
        streamRes.write(sse({ type: "session.status", status: "running", response_id: "resp_1" }));
        streamRes.write(sse({ type: "response.output_text.delta", delta: "Hello ", message_id: "m1", index: 0 }));
        streamRes.write(sse({ type: "session.status", status: "idle" }));
        streamRes.write(sse({ type: "response.output_text.delta", delta: "world", message_id: "m1", index: 1 }));
        streamRes.write(
          `event: session.status\ndata: ${JSON.stringify({
            type: "session.status",
            data: { status: "idle", response_id: "resp_1" },
          })}\n\n`,
        );
        return;
      }
      return json(res, 404, {});
    },
    async (mcp) => {
      await handshake(mcp);
      const result = await callTool(mcp, "send_message", {
        session_id: "conv_1",
        text: "hi",
        confirm: true,
        wait: true,
        timeout_seconds: 3,
      });
      assert.equal(result.reply, "Hello world");
      assert.equal(result.status, "idle");
      assert.equal(result.timed_out, false);
      assert.equal(result.reply_source, "stream");
      assert.equal(result.end_reason, "idle");
      assert.deepEqual(result.accepted, { queued: true, item_id: "msg_1" });
      const post = seen.find((hit) => hit.method === "POST");
      const stream = seen.find((hit) => hit.url.includes("/stream"));
      assert.ok(seen.indexOf(stream) < seen.indexOf(post));
      assert.equal(stream.url, "/v1/sessions/conv_1/stream?idle=false");
      assert.deepEqual(post.body, {
        type: "message",
        data: { role: "user", content: [{ type: "input_text", text: "hi" }] },
      });
      assert.equal(seen.some((hit) => hit.url.includes("/items")), false);
    },
  );
});

test("send_message wait records a response.failed error", { timeout: 15000 }, async () => {
  let streamRes;
  await withMock(
    async (req, res) => {
      if (req.url.includes("/stream")) {
        streamRes = res;
        res.writeHead(200, { "content-type": "text/event-stream" });
        // Headers are not flushed until the first byte. A comment opens the stream.
        res.write(": open\n\n");
        return;
      }
      if (req.method === "POST") {
        await readBody(req);
        json(res, 202, { queued: true, item_id: "msg_1" });
        streamRes.write(sse({ type: "session.status", status: "running" }));
        streamRes.write(sse({ type: "response.output_text.delta", delta: "Not logged in", message_id: "m1", index: 0 }));
        streamRes.write(
          sse({
            type: "response.failed",
            response: { error: { message: "Not logged in · Please run /login" } },
          }),
        );
        return;
      }
      return json(res, 404, {});
    },
    async (mcp) => {
      await handshake(mcp);
      const failed = await callTool(mcp, "send_message", {
        session_id: "conv_1",
        text: "ping",
        confirm: true,
        wait: true,
        timeout_seconds: 3,
      });
      assert.equal(failed.status, "failed");
      assert.equal(failed.end_reason, "response.failed");
      assert.equal(failed.error, "Not logged in · Please run /login");
      assert.equal(failed.reply, "Not logged in");
      assert.equal(failed.timed_out, false);
    },
  );
});

test("send_message wait stops on an elicitation and on timeout", { timeout: 15000 }, async () => {
  let streamRes;
  await withMock(
    async (req, res) => {
      if (req.url.includes("/stream")) {
        streamRes = res;
        res.writeHead(200, { "content-type": "text/event-stream" });
        res.write(sse({ type: "session.status", status: "running" }));
        return;
      }
      if (req.method === "POST") {
        await readBody(req);
        json(res, 202, { queued: true });
        streamRes.write(
          sse({
            type: "response.elicitation_request",
            elicitation_id: "el_1",
            params: { message: "Allow the command?" },
          }),
        );
        return;
      }
      return json(res, 404, {});
    },
    async (mcp) => {
      await handshake(mcp);
      const parked = await callTool(mcp, "send_message", {
        session_id: "conv_1",
        text: "run",
        confirm: true,
        wait: true,
        timeout_seconds: 3,
      });
      assert.equal(parked.end_reason, "elicitation");
      assert.equal(parked.needs_input.elicitation_id, "el_1");
      assert.equal(parked.needs_input.message, "Allow the command?");
      assert.equal(parked.timed_out, false);
    },
  );

  await withMock(
    (req, res) => {
      if (req.url.includes("/stream")) {
        res.writeHead(200, { "content-type": "text/event-stream" });
        res.write(": ping\n\n");
        return;
      }
      if (req.method === "POST") {
        readBody(req).then(() => json(res, 202, { queued: true }));
        return;
      }
      return json(res, 404, { error: "missing" });
    },
    async (mcp) => {
      await handshake(mcp);
      const timed = await callTool(mcp, "send_message", {
        session_id: "conv_1",
        text: "hang",
        confirm: true,
        wait: true,
        timeout_seconds: 0.4,
      });
      assert.equal(timed.timed_out, true);
      assert.equal(timed.end_reason, "timeout");
      assert.equal(timed.reply, "");
    },
  );
});

test("send_message wait recovers a reply from items when the stream already closed", { timeout: 15000 }, async () => {
  await withMock(
    async (req, res) => {
      if (req.url.includes("/stream")) {
        res.writeHead(200, { "content-type": "text/event-stream" });
        res.end(sse({ type: "session.status", status: "idle" }));
        return;
      }
      if (req.method === "POST") {
        await readBody(req);
        return json(res, 202, { queued: true, item_id: "msg_user" });
      }
      if (req.url.includes("/items")) {
        const query = new URL(req.url, "http://127.0.0.1").searchParams;
        assert.equal(query.get("order"), "desc");
        return json(res, 200, {
          object: "list",
          data: [
            { id: "msg_b", type: "message", role: "assistant", content: [{ type: "output_text", text: "fresh" }] },
            { id: "msg_u", type: "message", role: "user", content: [{ type: "input_text", text: "hi" }] },
            { id: "msg_a", type: "message", role: "assistant", content: [{ type: "output_text", text: "old" }] },
          ],
          has_more: false,
        });
      }
      return json(res, 404, {});
    },
    async (mcp) => {
      await handshake(mcp);
      const result = await callTool(mcp, "send_message", {
        session_id: "conv_1",
        text: "hi",
        confirm: true,
        wait: true,
        timeout_seconds: 3,
      });
      assert.equal(result.reply, "fresh");
      assert.equal(result.reply_source, "items");
      assert.equal(result.timed_out, false);
    },
  );
});

test("tool and HTTP failures are tool errors", { timeout: 15000 }, async () => {
  await withMock(
    (req, res) => json(res, 422, { error: { message: "workspace is relative" } }),
    async (mcp) => {
      await handshake(mcp);
      assert.match(await callToolError(mcp, "missing_tool"), /Unknown tool/);
      assert.match(await callToolError(mcp, "send_message", { session_id: "conv_1" }), /text is required/);
      assert.match(
        await callToolError(mcp, "list_sessions", { visibility: "nope" }),
        /visibility must be one of/,
      );
      const http = await callToolError(mcp, "health");
      assert.match(http, /HTTP 422/);
      assert.match(http, /workspace is relative/);
    },
  );
});

const ACCESS_TOKEN = "mcp-test-token-do-not-log";
const SESSION_COOKIE = "mcp-test-cookie-do-not-log";

test("accounts auth sends the bearer token or session cookie and does not log them", { timeout: 15000 }, async () => {
  const http = loadConfig({ OMNIGENT_URL: "http://127.0.0.1:6767", OMNIGENT_SESSION_COOKIE: SESSION_COOKIE });
  assert.equal(http.headers.cookie, `ap_session=${SESSION_COOKIE}`);
  const https = loadConfig({
    OMNIGENT_URL: "https://example.ngrok.io",
    OMNIGENT_SESSION_COOKIE: SESSION_COOKIE,
    OMNIGENT_ACCESS_TOKEN: `  ${ACCESS_TOKEN}  `,
  });
  assert.equal(https.headers.cookie, `__Host-ap_session=${SESSION_COOKIE}`);
  assert.equal(https.headers.authorization, `Bearer ${ACCESS_TOKEN}`);
  const pair = loadConfig({
    OMNIGENT_URL: "https://example.ngrok.io",
    OMNIGENT_SESSION_COOKIE: `ap_session=${SESSION_COOKIE}`,
  });
  assert.equal(pair.headers.cookie, `ap_session=${SESSION_COOKIE}`);
  assert.equal(pair.headers.authorization, undefined);
  assert.throws(() => loadConfig({ OMNIGENT_ACCESS_TOKEN: "bad\ntoken" }), /OMNIGENT_ACCESS_TOKEN contains a newline/);

  const seen = [];
  await withMock(
    (req, res) => {
      seen.push({ url: req.url, authorization: req.headers.authorization, cookie: req.headers.cookie });
      if (req.url === "/health") return json(res, 401, { error: "unauthorized", message: "Authentication required" });
      return json(res, 200, { object: "list", data: [], has_more: false });
    },
    async (mcp) => {
      await handshake(mcp);
      const failure = await callToolError(mcp, "health");
      assert.match(failure, /HTTP 401/);
      assert.match(failure, /Authentication required/);
      assert.equal(failure.includes(ACCESS_TOKEN), false);
      assert.equal(failure.includes(SESSION_COOKIE), false);
      const sessions = await callTool(mcp, "list_sessions");
      assert.deepEqual(sessions.data, []);
      assert.equal(seen[0].authorization, `Bearer ${ACCESS_TOKEN}`);
      assert.equal(seen[0].cookie, `ap_session=${SESSION_COOKIE}`);
      assert.equal(mcp.stderr.includes(ACCESS_TOKEN), false);
      assert.equal(mcp.stderr.includes(SESSION_COOKIE), false);
    },
    { env: { OMNIGENT_ACCESS_TOKEN: ACCESS_TOKEN, OMNIGENT_SESSION_COOKIE: SESSION_COOKIE } },
  );
});

function agentPages(req, res, pages) {
  const query = new URL(req.url, "http://127.0.0.1").searchParams;
  if (query.get("scope") === "user") {
    return json(res, 200, { object: "list", data: pages.user ?? [], has_more: false });
  }
  const after = query.get("after");
  const page = after ? pages.next : pages.first;
  return json(res, 200, page);
}

test("create_session accepts the agent name list_agents reports", { timeout: 15000 }, async () => {
  const debby = "eac9e787e68ae6774d77e618031c287a";
  const seen = [];
  await withMock(
    (req, res) => {
      seen.push({ method: req.method, url: req.url });
      if (req.url.startsWith("/v1/agents")) {
        return agentPages(req, res, {
          first: {
            object: "list",
            data: [{ id: "ag_other", name: "other" }],
            has_more: true,
            last_id: "ag_other",
          },
          next: {
            object: "list",
            data: [{ id: debby, name: "debby", harness: "claude-sdk" }],
            has_more: false,
            last_id: debby,
          },
          user: [{ id: "ag_user", name: "notes", harness: "codex" }],
        });
      }
      if (req.method === "POST" && req.url === "/v1/sessions") {
        return json(res, 201, { id: "conv_named", agent_id: debby, status: "idle", items: [] });
      }
      return json(res, 404, {});
    },
    async (mcp) => {
      await handshake(mcp);
      const preview = await callTool(mcp, "create_session", { agent_name: "debby", title: "named" });
      assert.equal(preview.dry_run, true);
      assert.equal(preview.body.agent_id, debby);
      assert.equal(preview.method, "POST");
      const created = await callTool(mcp, "create_session", { name: "Debby", confirm: true });
      assert.equal(created.session.id, "conv_named");
      assert.equal(created.session.agent_id, debby);
      const urls = seen.map((hit) => hit.url);
      assert.ok(urls.includes("/v1/agents?limit=1000"));
      assert.ok(urls.includes("/v1/agents?limit=1000&after=ag_other"));
      assert.ok(urls.includes("/v1/agents?limit=1000&scope=user"));
      assert.equal(seen.filter((hit) => hit.method === "POST").length, 1);
      const missing = await callToolError(mcp, "create_session", { title: "none" });
      assert.match(missing, /agent_id or agent_name/);
      assert.match(missing, /data\[\]\.id from list_agents/);
      assert.match(missing, /data\[\]\.name from list_agents/);
      const unknown = await callToolError(mcp, "create_session", { agent_name: "missing" });
      assert.match(unknown, /No agent named "missing"/);
      assert.match(unknown, /data\[\]\.name/);
    },
  );
});

test("create_session names the field when several agents share a name", { timeout: 15000 }, async () => {
  await withMock(
    (req, res) => {
      if (!req.url.startsWith("/v1/agents")) return json(res, 404, {});
      return agentPages(req, res, {
        first: {
          object: "list",
          data: [
            { id: "ag_a", name: "debby", harness: "claude-sdk" },
            { id: "ag_b", name: "debby", harness: "codex" },
          ],
          has_more: false,
          last_id: "ag_b",
        },
      });
    },
    async (mcp) => {
      await handshake(mcp);
      const error = await callToolError(mcp, "create_session", { agent_name: "debby" });
      assert.match(error, /Several agents are named "debby"/);
      assert.match(error, /Pass agent_id/);
      assert.match(error, /ag_a \(claude-sdk\)/);
      assert.match(error, /ag_b \(codex\)/);
    },
  );
});

test("tools/call accepts omitted arguments for health", { timeout: 15000 }, async () => {
  await withMock(
    (req, res) => json(res, 200, { status: "ok" }),
    async (mcp) => {
      await handshake(mcp);
      const response = await mcp.request("tools/call", { name: "health" });
      assert.notEqual(response.result.isError, true);
      assert.deepEqual(JSON.parse(response.result.content[0].text), { status: "ok" });
    },
  );
});
