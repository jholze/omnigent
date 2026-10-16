#!/usr/bin/env node
/**
 * Stdio MCP server (newline-delimited JSON-RPC, plus Content-Length frames).
 * Logs go to stderr. stdout is reserved for protocol messages.
 */

import { callTool, loadConfig, toolDefinitions } from "./omnigent.js";

const SERVER_INFO = { name: "omnigent", version: "0.1.0" };

let configPromise;

function getConfig() {
  if (!configPromise) {
    configPromise = Promise.resolve().then(() => loadConfig());
  }
  return configPromise;
}

function send(message, framing) {
  const body = JSON.stringify(message);
  if (framing === "content-length") {
    process.stdout.write(`Content-Length: ${Buffer.byteLength(body, "utf8")}\r\n\r\n${body}`);
    return;
  }
  process.stdout.write(`${body}\n`);
}

function initializeResult(params) {
  const requested = params?.protocolVersion;
  return {
    protocolVersion: typeof requested === "string" && requested ? requested : "2024-11-05",
    capabilities: { tools: { listChanged: false } },
    serverInfo: SERVER_INFO,
    instructions:
      "Drives a running Omnigent server. OMNIGENT_URL defaults to http://127.0.0.1:6767. " +
      "create_session, send_message, interrupt_turn, and stop_session do nothing unless confirm is true; " +
      "they return the method, path, and body they would send. " +
      "A user message is POST /v1/sessions/{id}/events with type message. " +
      "send_message wait:true opens the session SSE stream before that POST and reads until the turn ends.",
  };
}

function toolResult(text, isError = false) {
  const result = { content: [{ type: "text", text }] };
  if (isError) result.isError = true;
  return result;
}

async function dispatch(method, params) {
  if (method === "initialize") return initializeResult(params);
  if (method === "ping") return {};
  if (method === "notifications/initialized" || method.startsWith("notifications/")) return {};
  if (method === "tools/list") return { tools: toolDefinitions() };
  if (method === "tools/call") return performCall(params);
  const error = new Error(`Method not found: ${method}`);
  error.rpcCode = -32601;
  throw error;
}

async function performCall(params) {
  const name = params?.name;
  const args = params?.arguments ?? {};
  if (typeof name !== "string" || !name) return toolResult("tool name is required", true);
  if (typeof args !== "object" || args === null || Array.isArray(args)) {
    return toolResult("arguments must be an object", true);
  }
  try {
    const config = await getConfig();
    const data = await callTool(name, args, config);
    return toolResult(JSON.stringify(data, null, 2));
  } catch (error) {
    return toolResult(error instanceof Error ? error.message : String(error), true);
  }
}

function rpcError(id, code, message, framing) {
  send({ jsonrpc: "2.0", id, error: { code, message } }, framing);
}

async function onMessage(raw, framing) {
  let message;
  try {
    message = JSON.parse(raw);
  } catch {
    rpcError(null, -32700, "Parse error", framing);
    return;
  }
  if (!message || typeof message !== "object" || Array.isArray(message)) {
    rpcError(null, -32600, "Invalid request", framing);
    return;
  }
  const hasId = Object.hasOwn(message, "id") && message.id !== null && message.id !== undefined;
  if (message.jsonrpc !== "2.0" || typeof message.method !== "string") {
    if (hasId) rpcError(message.id, -32600, "Invalid request", framing);
    return;
  }
  try {
    const result = await dispatch(message.method, message.params ?? {});
    if (hasId) send({ jsonrpc: "2.0", id: message.id, result }, framing);
  } catch (error) {
    if (!hasId) return;
    rpcError(message.id, error.rpcCode ?? -32603, error.message || "Internal error", framing);
  }
}

let chain = Promise.resolve();
let buffer = Buffer.alloc(0);

function enqueue(raw, framing) {
  chain = chain
    .then(() => onMessage(raw, framing))
    .catch((error) => {
      process.stderr.write(`omnigent-mcp: ${error?.stack || error}\n`);
    });
}

function drain() {
  while (buffer.length > 0) {
    if (buffer[0] === 0x0a || buffer[0] === 0x0d) {
      buffer = buffer.subarray(1);
      continue;
    }
    const prefix = buffer.subarray(0, Math.min(buffer.length, 24)).toString("utf8").toLowerCase();
    if (prefix.startsWith("content-length:")) {
      const headerEnd = buffer.indexOf("\r\n\r\n");
      if (headerEnd === -1) return;
      const header = buffer.subarray(0, headerEnd).toString("utf8");
      const match = /content-length:\s*(\d+)/i.exec(header);
      if (!match) {
        buffer = buffer.subarray(headerEnd + 4);
        continue;
      }
      const length = Number(match[1]);
      const start = headerEnd + 4;
      if (buffer.length < start + length) return;
      const body = buffer.subarray(start, start + length).toString("utf8");
      buffer = buffer.subarray(start + length);
      enqueue(body, "content-length");
      continue;
    }
    const newline = buffer.indexOf(0x0a);
    if (newline === -1) return;
    const line = buffer.subarray(0, newline).toString("utf8").replace(/\r$/, "");
    buffer = buffer.subarray(newline + 1);
    if (line.trim()) enqueue(line, "ndjson");
  }
}

process.stdin.on("data", (chunk) => {
  buffer = Buffer.concat([buffer, chunk]);
  drain();
});

process.stdin.on("end", () => {
  chain.finally(() => process.exit(0));
});

process.stdin.on("error", (error) => {
  process.stderr.write(`omnigent-mcp stdin: ${error.message}\n`);
});
