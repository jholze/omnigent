# Omnigent MCP server

A zero-dependency stdio MCP server that lets another agent (Claude Code, Cursor, Codex, and anything else that speaks MCP) drive a running Omnigent server: list agents, hosts, and sessions, read a transcript, create a session, and post a user message.

It speaks newline-delimited JSON-RPC on stdin/stdout and also accepts `Content-Length` frames. Nothing is written to stdout except protocol messages.

## Configure

The server is `integrations/mcp/src/server.js`. Replace the path below with the absolute path on your machine. `OMNIGENT_URL` defaults to `http://127.0.0.1:6767`.

Claude Code (`.mcp.json`) and Cursor (`.cursor/mcp.json`) use the same shape:

```json
{
  "mcpServers": {
    "omnigent": {
      "command": "node",
      "args": ["/absolute/path/to/omnigent/integrations/mcp/src/server.js"],
      "env": {
        "OMNIGENT_URL": "http://127.0.0.1:6767"
      }
    }
  }
}
```

A local `omnigent server` in header mode has no API key. Identity falls back to the reserved `local` user. Behind a proxy that injects an email, set `OMNIGENT_EMAIL` (sent as `X-Forwarded-Email`). If the proxy uses a different header name, set `OMNIGENT_AUTH_HEADER` as well. That header does not authenticate an accounts server.

```json
"env": {
  "OMNIGENT_URL": "https://omnigent.example.com",
  "OMNIGENT_EMAIL": "you@example.com"
}
```

### Accounts login (username and password)

When `GET /v1/info` reports `accounts_enabled: true` and `login_url: "/login"`, the server ignores `X-Forwarded-Email`. Browser sessions use the cookie `ap_session` on plain HTTP and `__Host-ap_session` on HTTPS (including an ngrok URL). `omnigent login`, `omnigent host`, and `omnigent attach` use the same session JWT as `Authorization: Bearer`. That bearer is the credential this MCP server sends. There is no separate personal access token.

On the Mac that runs the server, log in with the CLI against the same base URL you will put in `OMNIGENT_URL`. Accounts login is a username and password prompt. It does not open a browser.

```bash
omnigent login "https://YOUR-NGROK-HOST"
```

Use `http://127.0.0.1:6767` when the MCP server will call the server on localhost. The username defaults to `admin`. If the first account was created as your macOS user, enter that name instead. The password is the one you set for that account.

The CLI writes `~/.omnigent/auth_tokens.json` (mode `0600`). The object is keyed by the server URL you passed to `omnigent login`. Copy the `token` string for that URL. Leave `refresh_token` in the file. `omnigent host` refreshes on its own. This MCP server does not, so it needs the current `token`.

Put it in the MCP env. Do not commit it, and do not paste it into a chat or a log.

```json
"env": {
  "OMNIGENT_URL": "https://YOUR-NGROK-HOST",
  "OMNIGENT_ACCESS_TOKEN": "<token field from ~/.omnigent/auth_tokens.json>"
}
```

The access token is the session JWT. The default lifetime is 8 hours (`expires_at` in the same file, `expires_in` from `POST /auth/login`). When authenticated reads start returning HTTP 401, run `omnigent login` again and replace `OMNIGENT_ACCESS_TOKEN`. A token minted against localhost is valid for the ngrok URL of that same server. Log in against the URL you configure so the file entry you copy matches `OMNIGENT_URL`.

To use the browser cookie instead, copy its value (the JWT, not the cookie name) into `OMNIGENT_SESSION_COOKIE`. On `http://` the client sends `ap_session`. On `https://` it sends `__Host-ap_session`. A value that already starts with `ap_session=` or `__Host-ap_session=` is sent unchanged. Either env var is enough. If both are set, both headers are sent. The values are request headers only. They are not written to stdout or stderr.

Requires Node.js 18 or newer. There are no npm dependencies.

## Dry-run

`create_session`, `send_message`, `interrupt_turn`, and `stop_session` do nothing unless the argument `confirm` is the boolean `true`. Without it, the tool returns the request it would send and does not call Omnigent:

```json
{ "dry_run": true, "method": "POST", "path": "/v1/sessions", "body": {} }
```

Read tools run immediately.

## Tools

| Tool | HTTP | Notes |
| --- | --- | --- |
| `health` | `GET /health` | Liveness. |
| `info` | `GET /v1/info` | Version, auth mode, single-user flag. |
| `list_agents` | `GET /v1/agents` | Optional `scope: "user"`. |
| `list_hosts` | `GET /v1/hosts` | Online and offline hosts. |
| `list_harnesses` | `GET /v1/harnesses` | Installed harnesses. |
| `list_sessions` | `GET /v1/sessions` | Filters: `agent_id`, `agent_name`, `search_query`, `project`, `kind`, `order`, `sort_by`, `include_archived`, cursors. `visibility` is always sent (`all` by default, or `mine` / `shared` / `archived`). |
| `get_session` | `GET /v1/sessions/{id}` | Drops the embedded `items` array. |
| `get_session_items` | `GET /v1/sessions/{id}/items` | Newest 30 items by default (`order: "desc"`). Text blocks are truncated (`max_chars`, default 2000). |
| `create_session` | `POST /v1/sessions` | `agent_id` (`list_agents` `data[].id`) or `agent_name` / `name` (`data[].name`, for example `debby`). A name is looked up before the dry-run preview. Optional `host_id` + absolute `workspace`, `host_type`, `title`, and first `message`. |
| `send_message` | `POST /v1/sessions/{id}/events` | Posts a user message. Optional `wait`. |
| `interrupt_turn` | `POST /v1/sessions/{id}/events` | Body `{ "type": "interrupt" }`. Cancels the current turn. |
| `stop_session` | `POST /v1/sessions/{id}/events` | Body `{ "type": "stop_session" }`. Stops the live process. Owner-only. The conversation is kept. |

### Sending a message

User text is not the review-comment API (`/comments`). A turn is a session event:

```json
POST /v1/sessions/{session_id}/events
{ "type": "message", "data": { "role": "user", "content": [{ "type": "input_text", "text": "..." }] } }
```

That route is intentionally absent from `openapi.json` (`include_in_schema` is false). The same body is what `create_session` puts in `initial_items` when `message` is set.

`send_message` with `wait: true` opens `GET /v1/sessions/{id}/stream?idle=false` **before** the POST, so the turn's events are not missed. It returns when `session.status` settles to `idle` or `failed` for this turn. A stale `idle` replayed on connect, and a native PTY `idle` that has no `response_id` while a response is still open, are ignored. An elicitation (`response.elicitation_request`) ends the wait early with `needs_input` — this server does not approve it. `timeout_seconds` defaults to 120. If the stream closes without assistant text, the tool reads the newest items and returns the assistant message that follows this user text.

To wait on the first message, create the session without `message`, then `send_message` with `wait`. A message placed on create can finish before a later stream subscription sees it.

An external session that should actually run needs `host_id` and an absolute `workspace` (the server launches the runner). `host_type: "managed"` lets the server provision a sandbox; do not pass `host_id` in that case. A harness still needs its own model credentials before a turn can complete.

## Tests

```bash
cd integrations/mcp
npm test
```

`npm test` runs `node --test` against a mock HTTP server. It does not contact a real Omnigent.
