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
account, and session permissions apply. A local single-user server accepts
`/mcp` the same way it accepts `/v1` (no token when
`OMNIGENT_LOCAL_SINGLE_USER` is set).

The endpoint is on by default. Set `mcp_enabled: false` in the server config
file, or `OMNIGENT_MCP_ENABLED=0`, to turn it off. A disabled server answers
`/mcp` with `404`.

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
| `resolve_elicitation` | `POST /v1/sessions/{id}/elicitations/{elicitation_id}/resolve` |
| `update_session`, `delete_session` | Patch title, project, or archived; delete the session. `delete_branch` asks the host to remove a server-created worktree branch |
| `add_comment`, `update_comment`, `delete_comment`, `list_comments` | Review comments on a session |
| `list_session_files` | Files stored on the session. Upload stays on the multipart route |
| `get_session_github`, `list_session_github_changes`, `attach_session_pull_request` | GitHub state, the PR file list, and attaching a pull request |
| `list_environment_changes`, `get_environment_diff` | Environment change list and one file diff |
| `list_host_filesystem`, `list_host_worktrees`, `create_host_directory` | Host directory listing, git worktrees, and creating a directory. These need a live host |
| `get_project`, `create_project`, `update_project`, `delete_project`, `get_usage` | Project CRUD and the caller's usage report |
| `list_session_policies`, `create_session_policy`, `delete_session_policy`, `list_policy_registry` | Per-session policies and the built-in handler registry |
| `list_session_mcp_servers`, `add_session_mcp_server`, `delete_session_mcp_server` | MCP servers declared on the session's agent |
| `list_skills`, `list_extensions`, `get_extension` | Skills for a session or host, and installed extensions |
| `list_session_permissions`, `get_session_owner`, `grant_session_access`, `revoke_session_access` | Session grants. Needs the permissions store |
| `get_sharing`, `set_sharing` | Server sharing settings. A deployment-managed setting is refused by the route |

`confirm` must be the boolean `true`. Any other value, including the string
`"true"`, is a dry-run. That applies to every write tool, not only
`create_session` and `send_message`.
