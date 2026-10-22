# Grok-Bot Client Smoke (T-129) — Runbook

Source: `specs/omnigent-chat/07-tasks.md` — T-129 · Release R1 (MCP-Bot-Pfad #12, vorgezogen)
Depends on: T-105, T-106, T-107, T-125, T-126 (recommended after T-119, T-127, T-128)

## Status

**RUN** on 2026-10-22 against the R0 demo only.

The protocol checks below were executed. The client was a local Streamable HTTP
JSON-RPC process, not the Grok Bot product. The `ogc_` token was not put into
the Grok Bot secret configuration.

## Purpose

Automated tests only simulate the bot (T-107, T-121). Before launch, a **real**
Grok-Bot MCP client must run once against `/mcp` so protocol incompatibilities
(protocol version, auth header, handling of `isError`/429) surface now rather
than at launch weekend (R-18).

## Preconditions

- R1 candidate running on the **existing R0 test instance** with `team_chat` enabled.
- Pre-flight (should be green before the smoke):
  `uv run --no-sync pytest tests/server/test_mcp_chat_tools.py tests/server/test_mcp_protocol_versions.py -q`

## Procedure

1. Confirm the R1 candidate runs on the R0 test instance with `team_chat` enabled.
2. Jens creates the agent account `grok-smoke`. The `ogc_` token is stored **only**
   in the Grok Bot's secret configuration — never in chat, the repo, or logs.
3. Create test channel `#bot-smoke` with Jens and `grok-smoke` as members.
   Test user Anna posts a mention of the `grok-smoke` agent account.
4. A real Grok-Bot MCP client connects to `/mcp` over Streamable HTTP using the
   test token and, in order:
   - `initialize` — record the negotiated protocol version (from FR-097).
   - `tools/list` — must return exactly the same tool names as the same request
     with Jens' user JWT, including the seven `chat_*` tools
     (`chat_list_channels`, `chat_read`, `chat_post`, `chat_open_dm`,
     `chat_list_members`, `chat_unread`, `chat_mark_read`).
     Do **not** call any non-chat tools in this smoke.
   - `chat_unread` — must return Anna's message.
   - `chat_post` with `confirm: true` in the thread of Anna's message.
   - `chat_mark_read`.
5. If streak / rate-limit behavior is observed (`bot_streak_limit`, `rate_limited`),
   note it (only relevant once T-127 / T-119 are merged).
6. Revoke the test token.
7. Record the protocol below.

## Expected results (AC-069c)

- A protocol version from FR-097 is negotiated.
- `tools/list` contains exactly the same tool names as with Jens' user JWT,
  including the seven `chat_*` tools.
- `chat_unread` returns Anna's message.
- `chat_post` with `confirm: true` appears in `#bot-smoke` with the bot badge.
- After the test, the test token is revoked.

## Execution (2026-10-22)

Instance: R0 demo `127.0.0.1:6791`, public host
`https://least-ink-jesus-deer.trycloudflare.com`. Code
`f929354586f0e0fcce2953e165602d388ba9c1ee` (epic/team-chat, includes the
Settings "Agenten" UI). `team_chat` was true. Production `127.0.0.1:6767`
was left running and was not signaled.

Jens approved creating `grok-smoke` on this demo (22.10. 09:02). The account
was created as user `jens` with display name `Grok Smoke` and handle
`grok-smoke` (`POST /v1/chat/agent-accounts`, 201). Account id and principal
id: `agent:2f831e29c52643caa2e41052c9597d11`.

The demo process did not have `OMNIGENT_CHAT_AGENT_SECRET`, and account
minting refuses without it. A new HMAC secret was generated for this demo
process only and kept in the local demo env file (mode 600). That secret is
not the `ogc_` token and was not copied into the Grok Bot configuration.

The `ogc_` token was written only to
`~/.local/share/omnigent-chat-demo-20261020/grok-smoke.token` (mode 600).
It was not printed, committed, or placed in the Grok Bot secret config /
`process.env`.

`#bot-smoke` is channel `8283428c297b41d2a7ddc1b697567453`. Jens created it.
Anna joined with `POST /v1/chat/channels/{id}/join`. There is no HTTP route
that adds another principal, so `grok-smoke` was added with the chat store
`add_member` on the demo database. Anna posted
`<@agent_account:agent:2f831e29c52643caa2e41052c9597d11> sag Hallo`
(message `39398f1b1bef47889889d7ec7f74ff42`).

The MCP client was a local Streamable HTTP JSON-RPC caller
(`initialize`, `notifications/initialized`, `tools/list`, `tools/call`).
It read the token file and did not call any non-chat tool. This is not the
Grok Bot product. Step 4's product client was not run, because that client
would have needed the token in the Grok Bot secret config.

Directory label for the account was `Grok Smoke (Jens)` with kind
`agent_account`. The shipped label is the display name plus the owner id's
local-part (`jens` → `Jens`). It is not `Grok Smoke (Jens Holze)`.
`_human_label` was not changed.

The bot reply `Hallo Anna. T-129 smoke reply.` is message
`f9414aad18ec4c12a61ab850cc2720d7`, thread root
`39398f1b1bef47889889d7ec7f74ff42`, `author_kind` `agent_account`.
On the public thread page the name, the Bot badge, Anna's mention, and the
reply were visible at 1280×800 and at 390×844. Screenshots are local only
(`~/.local/share/omnigent-chat-demo-20261020/shots/t129/`) and are not in
this commit.

Pre-flight on this tree: `tests/server/test_mcp_chat_tools.py` and
`tests/server/test_mcp_protocol_versions.py` — 10 passed.

## Record

| Item | Value |
| --- | --- |
| Negotiated protocol version | `2025-11-25` (requested `2025-11-25`; `MCP-Protocol-Version` response header `2025-11-25`; `notifications/initialized` HTTP 202) |
| `tools/list` tool names | Same ordered list as Jens' user JWT (36 names). The seven `chat_*` tools are present. No non-chat tool was called. |
| `chat_unread` | One item: Anna's message `39398f1b1bef47889889d7ec7f74ff42` in `#bot-smoke`, author kind `human`, `content_trust` `untrusted`. |
| `chat_post` | `confirm: true` stored the thread reply `f9414aad18ec4c12a61ab850cc2720d7`. Not deduplicated. Bot badge visible at both viewports. |
| `chat_mark_read` | Cursor advanced to Anna's message. |
| Revocation check (expect 401 on `/mcp`) | Revoke HTTP 204. Next `initialize` with the same token: HTTP 401 `{"error":{"code":"unauthorized","message":"Authentication required"}}`. |
| Streak / rate-limit observations | Not observed. No `bot_streak_limit` or `rate_limited`, and no `Retry-After`. |
| Display label | `Grok Smoke (Jens)`, not `Grok Smoke (Jens Holze)`. |

`tools/list` names, in order, for both the agent token and Jens' user JWT:

`health`, `info`, `list_agents`, `list_hosts`, `list_harnesses`,
`list_projects`, `list_sessions`, `get_session`, `get_session_items`,
`create_session`, `send_message`, `interrupt_turn`, `stop_session`,
`get_elicitation`, `answer_elicitation`, `session_files`, `session_workspace`,
`session_github`, `manage_session`, `session_comments`, `host_fs`, `project`,
`policy`, `session_mcp`, `list_skills`, `list_extensions`, `get_extension`,
`session_access`, `sharing`, `chat_list_channels`, `chat_read`, `chat_post`,
`chat_open_dm`, `chat_list_members`, `chat_unread`, `chat_mark_read`.

## Result

**PASS with a client deviation.** Protocol, tool names, unread, confirmed
post, bot badge, mark-read, and revocation 401 all matched the checks above.
The caller was not the Grok Bot product, and the token never entered that
product's secret config. The on-screen author label is `Grok Smoke (Jens)`.

## Follow-up

Product-client run (Grok Bot itself as the MCP client, with a fresh ogc_ token in its secret config) is still open and must happen before the T-123 launch go. The orchestrator will arrange it.
