# Omnigent: bot-initiated sessions must not hang (delivery ack + stall watchdog)

Version: 0.2 | Date: 2026-10-31 | Status: Approved (Boss under Jens' delegation) | Repo: jholze/omnigent (fork)

## 1. Problem (evidence, session d97dd2f6 "OKR Odoo Module PM Hub")
All times CET.
- 21:26:35 task message #79 (user item, turn_642653d2) starts a turn. The assistant writes one line, then emits nothing more.
- 21:32:05 follow-up "Update from Jens ... #76 / PR 78" (the bot hand-over, turn_49845f74) is POSTed while that turn is still active. The runner buffers it behind the active turn. The POST itself succeeds; there is no delivery failure.
- 22:27:28 error item: "turn exceeded the 3600s harness idle watchdog (run_turn emitted no events for 3600s; likely a wedged LLM or tool call)".
- 22:44:49 the owner interrupts (a batch of read_file calls is cancelled), then sends "again" (22:44:53) and "ok go ahead" (22:45:46). The session is now `running`, runner_online=true, host_online=true, no pending elicitations, pending_inputs=[].
Root cause: the session was neither asleep nor offline. A wedged turn held the session as `running` and silent for 60 min (default `HARNESS_TURN_TIMEOUT_S`=3600, `_scaffold.py`). The bot's message waited behind that turn. Automatic wedge recovery (`_WEDGED_TURN_RECOVERY_RETRIES`=1) only covers pre-output wedges. This turn had already written output, so nothing retried it. The sender got back `queued:true` and had no way to see that the session was stalled.

## 2. What exists (fork, main 72aae3b09)
- Offline runner/host: the events route relaunches a disconnected runner (`routes_events.py` recovery → `runner_relaunched`) or fails fast with "host offline".
- Idle parent wake: `server/subagent_wake.py` (deduped through `omnigent.subagent_wake.*` labels).
- Harness idle watchdog of 3600 s, plus one retry for a pre-output wedge (`runtime/harnesses/_scaffold.py`). Forwarder POST failure attribution is in `native/_native_forwarder_health.py`.
- MCP `send_message` (`server/mcp_endpoint.py`) is a dry run unless `confirm` is set. Without `wait` it returns the raw POST result (`{queued:true,item_id}`). With `wait` it streams until idle/failed/elicitation/timeout.
- Manager webhook outbox (`session_outbox.py`): completed/failed/awaiting_decision/resumed.

## 3. Gap analysis
| Block | State | Note |
|---|---|---|
| (a) Delivery state in send_message result | adapt | `queued` exists. Missing: `delivery` = started / buffered_behind_turn / relaunched, plus active-turn age and last-event age |
| (b) Watchdog for buffered input not consumed > N s | new | nothing reacts before 3600 s |
| (c) Runner offline → relaunch | exists | on POST only. A periodic check is not needed for this case |
| (d) Visible `stalled` state + event | new | no stalled flag, no outbox event |
| (e) Event-POST retry/timeout | adapt | client POST timeouts appear in "Last Timeouts". An idempotent retry with item_id dedupe is needed |
| (f) Limits / no wake loop | new | |

## 4. Requirements
- FR-1 The send result reports the delivery state: `delivery` ∈ {turn_started, buffered, runner_relaunched}, `active_turn_age_s`, and `last_event_age_s`.
- FR-2 A server-side stall watchdog: when a session is `running` and its active turn has emitted no progress event for `STALL_AFTER_S` (default 600), the session is marked stalled.
- FR-3 When stalled AND user input is buffered behind the turn AND the input came from a bot/MCP actor, the server interrupts the stalled turn once. The buffered input then starts the next turn, the same way the owner's interrupt + "again" did today.
- FR-4 Stall visibility: label `omnigent.stall.state`, event `session.stalled` (outbox + live stream), and `stalled:true` in get_session and list_sessions.
- FR-5 Event POST from the MCP/SDK client: bounded retry (max 3, backoff) with an idempotency key, so a retry never duplicates an item.
- FR-6 Cost brake: at most 1 auto-interrupt per session per 30 min, and never while an elicitation is pending. After the limit, mark stalled only and notify.


## 4a. Binding decisions v0.2 [CHANGED v0.2 2026-10-31: Entscheidung Boss, approved by Boss under Jens' delegation]
- D-1 Stall threshold STALL_AFTER_S=300 (env OMNIGENT_STALL_AFTER_S): running turn with no event for 300 s => status/label "stalled" + event `session.stalled`. On next event => `session.recovered`. Mark+event always on.
- D-2 Auto-interrupt: if additionally input (bot OR owner) is buffered and >=600 s without event (OMNIGENT_STALL_INTERRUPT_AFTER_S), interrupt the turn once via the existing interrupt path; the buffered message then starts. Never with open elicitations/questions. Cost brake: max 1 automatic intervention per 30 min per session. Feature flag OMNIGENT_STALL_AUTO_INTERRUPT, default ON.
- D-3 Reporting: events session.stalled/session.recovered (live stream + outbox), manager webhook, and in send_message/get_session results `delivery` ∈ {turn_started, buffered_behind_active_turn, runner_restarted} plus `seconds_since_last_event`.
- D-4 Harness idle watchdog default 3600 -> 1200 s (configurable, HARNESS_TURN_TIMEOUT_S).
- D-5 Event POST (MCP + Python client): retry with Idempotency-Key, no duplicates.
- D-6 Turns emitting progress events are never stalled (test, AC-8).
- D-7 Independent of the team_chat flag; MCP/bot access stays isolated from team chat.
- Supersedes: FR-1 field names, FR-2 600->300, FR-3 bot-only -> bot or owner with 600 s, rollout "auto-interrupt off" -> default on. Open questions 1-4 resolved.
- AC-9 [CHANGED v0.2 2026-10-31: Entscheidung Boss, approved by Boss under Jens' delegation] Given HARNESS_TURN_TIMEOUT_S unset, Then the harness idle watchdog is 1200 s; env override works.
- AC-10 [CHANGED v0.2 2026-10-31: Entscheidung Boss, approved by Boss under Jens' delegation] Given stalled, When an event arrives, Then `session.recovered` is emitted once and stalled cleared.
- AC-11 [CHANGED v0.2 2026-10-31: Entscheidung Boss, approved by Boss under Jens' delegation] Given OMNIGENT_STALL_AUTO_INTERRUPT=0, Then no interrupt, but mark+event still happen.
- AC-12 [CHANGED v0.2 2026-10-31: Entscheidung Boss, approved by Boss under Jens' delegation] Given team_chat flag on or off, Then stall behaviour is identical.
- AC-13 [CHANGED v0.2 2026-10-31: Entscheidung Boss, approved by Boss under Jens' delegation] Given stalled/recovered, Then the manager webhook receives the event.

## 5. Acceptance criteria
- AC-1 [NEW v0.1 2026-10-31: case d97dd2f6] Given an idle session, When a bot calls send_message confirm=true, Then the result contains delivery=turn_started and an item_id.
- AC-2 [NEW v0.1 2026-10-31: case d97dd2f6] Given a running turn, When a bot sends a message, Then delivery=buffered and active_turn_age_s is set.
- AC-3 [NEW v0.1 2026-10-31: case d97dd2f6] Given a running turn with no progress for STALL_AFTER_S, When the watchdog ticks, Then the session label shows stalled and one `session.stalled` event is emitted.
- AC-4 [NEW v0.1 2026-10-31: case d97dd2f6] Given a stalled session with bot input buffered, When the watchdog ticks, Then the turn is interrupted once and the buffered item starts a new turn.
- AC-5 [NEW v0.1 2026-10-31: wake-loop guard] Given an auto-interrupt in the last 30 min, When the session stalls again, Then there is no interrupt, only stalled + event.
- AC-6 [NEW v0.1 2026-10-31: elicitation safety] Given a pending elicitation, When the watchdog ticks, Then nothing is interrupted and nothing is marked stalled.
- AC-7 [NEW v0.1 2026-10-31: event-post timeouts] Given the first POST times out after the server stored it, When the client retries with the same idempotency key, Then exactly one item exists.
- AC-8 [NEW v0.1 2026-10-31: no regression] Given a turn that keeps emitting progress (e.g. compaction heartbeat), When it runs > STALL_AFTER_S, Then it is not marked stalled.

## 6. Strategy
Build server-side, not in the bots, so that every caller (Odo, Udo, MCP, Slack) benefits. Step 1 (low risk): extend the send/POST result and get_session with delivery and age fields (FR-1, FR-4 read side). Step 2: a stall watchdog task in the server that reads the last-event timestamp per running session. It reuses the existing interrupt path (`interrupt_turn` event) and existing runner relaunch, with no new wake mechanism. Step 3: idempotent event POST in `mcp_endpoint.py` and the python client. Every timeout and limit is configurable by env var. Default rollout: mark stalled + emit the event only (auto-interrupt off). Auto-interrupt is turned on after one week of observation.

Files: `server/routes/sessions/routes_events.py` (delivery fields, idempotency), new `server/stall_watchdog.py`, `server/app.py` (task start), `server/session_outbox.py` (`session.stalled`), `server/mcp_endpoint.py` (result + retry), `sdks/python-client` (retry). Tests: `tests/server/test_stall_watchdog.py`, extend the send_message MCP tests and the events-route tests (AC-1..8).

Risks: interrupting a turn that is legitimately slow but silent (mitigation: AC-8, 600 s default, auto-interrupt off at first); a tool side effect running twice after the interrupt (only bot input triggers it, and only once); false stalls on native terminal sessions (use their existing progress events, test it).

## 7. Open questions
1. STALL_AFTER_S default of 600 s, or shorter (e.g. 300 s)?
2. Auto-interrupt only for bot/MCP input, or also for the owner's own queued messages?
3. Notification channel for `session.stalled`: manager webhook only, or also a message to the sending bot?
4. Should the harness idle watchdog itself drop from 3600 s to e.g. 1200 s?

## Changelog
| Version | Date | Change |
|---|---|---|
| 0.1 | 2026-10-31 | Initial draft from case d97dd2f6 analysis |
| 0.2 | 2026-10-31 | Binding decisions D-1..D-7, AC-9..AC-13 added (Entscheidung Boss, approved by Boss under Jens' delegation) |
