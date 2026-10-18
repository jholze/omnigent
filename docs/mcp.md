# Drive Omnigent over MCP

The server exposes a Streamable HTTP MCP endpoint at `/mcp`. An external
agent (Claude Code, Cursor, or another MCP client) can list agents, hosts,
harnesses, projects, and sessions; read a transcript; and, with `confirm:
true`, create a session, post a message, interrupt a turn, or stop a session.
Reads run immediately. Writes without `confirm: true` return the HTTP request
they would make and change nothing.

`/mcp` uses the same authentication as `/v1`. In accounts mode an anonymous
call is `401`. Send the session JWT from `omnigent login` as
`Authorization: Bearer`, or send the browser cookie (`ap_session` on HTTP,
`__Host-ap_session` on HTTPS, including ngrok). The acting user is that
account, and session permissions apply. A delegated scope token (a device
grant or a client-credentials token) is rejected: `/mcp` is not on the
delegated path allowlist. A local single-user server accepts `/mcp` the same
way it accepts `/v1` (no token when `OMNIGENT_LOCAL_SINGLE_USER` is set).

`POST /mcp` requires `Content-Type: application/json` (or
`application/*+json`). Any other type, including `text/plain`, is `415`.
A present `Origin` has to pass the same trusted-origin check as `/v1`. In
local single-user mode that means a loopback origin (`http://127.0.0.1`,
`http://localhost`, or `http://[::1]`); any other origin is `403`. A missing
`Origin` is allowed, which is how non-browser clients call the endpoint.
The in-process `/v1` call forwards that `Origin` when the client sent one
and omits it otherwise. It does not substitute an internal origin.

Session ids and other URL segments are one path segment: `.`, `..`, and
characters such as `/` are rejected before a request is built. Filesystem
paths reject `.` and `..` segments (including percent-encoded ones). The
dry-run preview shows the percent-encoded path the confirmed call sends.

`send_message` and `answer_elicitation` with `wait: true` read the session
(`GET /v1/sessions/{id}?include_items=false`) before subscribing to the live
stream, so a caller without read access never joins it. The wait stops when
the MCP client disconnects. `timeout_seconds` is checked only when `wait`
is true.

The endpoint is on by default. Turn it off with `mcp_enabled: false` in the
server config, or set `OMNIGENT_MCP_ENABLED` to `0`, `false`, `no`, or
`off`. The env var and the config key both also accept `1`, `true`, `yes`,
and `on`. Any other string turns the endpoint off. A disabled server answers
`/mcp` with `404` before the content-type and origin checks.

## Get a token

```bash
omnigent login https://<server>
```

The CLI stores the session JWT in `~/.omnigent/auth_tokens.json`, keyed by
the server URL. Copy that entry's `token` into the client header below. On
the owner's server those tokens do not expire. A stock accounts server mints
a session JWT whose lifetime is `OMNIGENT_ACCOUNTS_SESSION_TTL_HOURS`
(default 8). `omnigent login` also stores a `refresh_token` when the server
issues one, and later CLI commands renew the session JWT with it.

## Claude Code

Add this to the Claude Code MCP config (user or project `.mcp.json`):

```json
{
  "mcpServers": {
    "omnigent": {
      "type": "http",
      "url": "https://<server>/mcp",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

Use `http://127.0.0.1:6767/mcp` for a server on the same machine.

## Cursor

Add this to `~/.cursor/mcp.json` (or the project `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "omnigent": {
      "url": "https://<server>/mcp",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

## Tools

Core session drive stays one tool per action. The rest of `/v1` is grouped
behind an `action` argument so the catalog stays short. A write action does
nothing unless `confirm` is boolean `true`. A delete or revoke preview
includes `will_lose`, a sentence naming what that call removes.

| Tool | Effect |
| --- | --- |
| `health`, `info` | `GET /health`, `GET /v1/info` |
| `list_agents`, `list_hosts`, `list_harnesses` | The matching `GET /v1/...` lists |
| `list_projects` | `GET /v1/projects`. Rows are `id`, `name`, `workspace`, `default_agent` (`config.workspace` and `config.agentId`) |
| `list_sessions` | Compact rows: `id`, `title`, `status`, `agent_name`, `project_id`, `working_directory`, `updated_at`. `project` / `project_id` filters to one project (an id from `list_projects`, or the project name). `working_directory` is parsed from the `omnigent.composer_context.v1.0` label |
| `get_session` | One session, without the embedded transcript |
| `get_session_items` | Newest 30 items by default, text truncated |
| `create_session` | `POST /v1/sessions`. `agent_id` or `agent_name`, plus optional `host_id`, `workspace`, and `message`. Dry-run unless `confirm` is true |
| `send_message` | `POST /v1/sessions/{id}/events` with `type: "message"` and an `input_text` block. `wait: true` reads the live stream until `session.status` is `idle` or `failed`, an elicitation arrives (`needs_input`), or `timeout_seconds` (default 120) |
| `interrupt_turn`, `stop_session` | `{type: "interrupt"}` / `{type: "stop_session"}` on the events route. Dry-run unless `confirm` is true |
| `get_elicitation` | `GET .../elicitations/{elicitation_id}` |
| `answer_elicitation` | `POST .../elicitations/{id}/resolve`. `approve`/`deny` map to `accept`/`decline`. `text` is sent as `content.text`. `wait: true` subscribes to the live stream before the verdict and reads until the turn ends |
| `session_files` | `list`, `get`, `content`, `delete` on `.../resources/files`. `delete` needs confirm |
| `session_workspace` | `list`, `read`, `search`, `changes`, `diff`, `write`, `edit`, `delete`, `shell` on `.../resources/environments/{environment_id}`. `environment_id` defaults to `default`. `write`, `edit`, `delete`, and `shell` need confirm. `shell` is owner-only and has no approval gate |
| `session_github` | `info`, `changes`, `diff`, `attach_pr`, `preferences` on `.../resources/github`. `attach_pr` and `preferences` need confirm |
| `manage_session` | `update` (title, archived, project, labels, model_override), `fork`, `delete`, `children`, `agent`, `labels`, `reset_model`. `update`, `fork`, `delete`, and `reset_model` need confirm. `delete_branch` also removes a server-created worktree branch |
| `session_comments` | `list`, `add`, `update`, `delete`, `send`. `send` is `POST .../comments/send`: it marks comments addressed and returns `formatted_message`. It does not post a turn |
| `host_fs` | `list` (`GET /v1/hosts/{id}/filesystem` and `.../filesystem/{path}`), `worktrees`, `mkdir`. `mkdir` needs confirm. These need a live host |
| `project` | `get`, `create`, `update`, `delete`, `order`, `usage`. `order` with no `ordered_project_ids` reads `GET /v1/projects/order`; a list or `null` is `PUT`. `usage` is `GET /v1/usage`. `delete` leaves member sessions unfiled |
| `policy` | `list`, `get`, `create`, `update`, `delete`, `evaluate`, `registry`, with `scope` `session` (default) or `global`. `evaluate` is per session (`POST .../policies/evaluate`) and can rewrite content. `registry` is `GET /v1/policy-registry`. Writes need confirm |
| `session_mcp` | `list`, `create`, `update`, `delete` on `.../agent/mcp-servers`. Writes change the agent bundle and need confirm |
| `list_skills` | `GET /v1/skills`. Pass `session_id`, or `host_id` plus `harness` and `path` |
| `list_extensions`, `get_extension` | `GET /v1/extensions` and `GET /v1/extensions/{id}` |
| `session_access` | `list`, `owner`, `grant`, `revoke` on `.../permissions`. `grant` and `revoke` need confirm. Level is 1–3 |
| `sharing` | `get` or `set` on `/v1/sharing`. `set` needs confirm. A deployment-managed setting is refused by the route |

`confirm` must be the boolean `true`. Any other value, including the string
`"true"` and the number `1`, is a dry-run.

### What the API does not do

These were dropped or renamed after checking the routes and `openapi.json`:

* There is no JSON session-file upload. `POST .../resources/files` is multipart.
* Replacing a session agent is `PUT .../agent` with a `.tar.gz` bundle, not a JSON body. `manage_session` `agent` is the read. MCP servers on that agent are `session_mcp`.
* `comments/send` does not deliver text to the agent. It marks the comments addressed and returns `formatted_message`. Post that text with `send_message`.
* Labels are read with `manage_session` `labels` (`GET .../labels`). Writing them is `manage_session` `update`, which merges `labels` through `PATCH /v1/sessions/{id}`. There is no labels write route.
* `reset_model` is `POST .../model-override/reset`. It clears `expected_model_override` only while that selection is still current. To clear unconditionally, `update` with `model_override` set to `default`, `off`, or `reset`.
* There is no global policy evaluate route. `policy` `evaluate` is per session. Global `create` accepts `type=python` and a handler from `policy` `registry`; a URL handler is rejected by the route.
* `session_workspace` `diff` and `session_github` `diff` call routes that are mounted with `include_in_schema=false`. They are real, and they are absent from `openapi.json`.
* `files:copy` is a lineage copy into a child session, not a general file write, so it is not a tool.
