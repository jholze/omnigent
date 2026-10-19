# Manager webhook

The server can push session lifecycle events to an HTTPS endpoint you
control. A receiver such as an assistant bot learns, within a fraction of a
second, when a session needs a human decision, when that decision is
recorded, when the session resumes, and when a turn completes or fails.
The receiver answers an elicitation by calling the resolve route named in
the payload.

Delivery is off by default. Nothing is written or sent until
`manager_webhook.enabled` is true and
`OMNIGENT_MANAGER_WEBHOOK_SECRET` is set.

The event names `session.completed`, `session.failed`,
`session.awaiting_decision`, and `session.resumed`, and the
`X-Omnigent-Signature: v1=<hex>` scheme, match the upstream durable
session-lifecycle push. `session.decision_resolved` is a fork extension.

## Setup

Add a block to the server config (`~/.omnigent/config.yaml`, or the file
in `OMNIGENT_CONFIG`):

```yaml
manager_webhook:
  enabled: true
  endpoint: https://bot.example.com/omnigent/webhook
  # Optional id echoed as X-Omnigent-Key-Id. Not a secret.
  key_id: grok-bot
  # Origin used to build the web approval link. No trailing path.
  public_base_url: https://omnigent.example.com
  # Omit to subscribe to every event. Unknown names are rejected at startup.
  events:
    - session.awaiting_decision
    - session.decision_resolved
    - session.resumed
    - session.completed
    - session.failed
  timeout_seconds: 10
  max_attempts: 10
  summary_max_chars: 500
  extra_headers:
    X-Receiver-Route: grok-bot
  # Tests only. Allows http://127.0.0.1, http://localhost, or http://[::1].
  allow_insecure_localhost: false
```

Export the signing key in the server's environment. Do not put it in the
YAML file.

```bash
export OMNIGENT_MANAGER_WEBHOOK_SECRET="$(openssl rand -hex 32)"
# Optional, only while rotating. Verification accepts either key.
# Delivery always signs with the current key.
export OMNIGENT_MANAGER_WEBHOOK_SECRET_PREVIOUS=""
```

Restart is not required to pick up a config edit. While the feature is
enabled, the dispatcher re-reads the file about once a second and polls
the outbox every couple of seconds for retries. While it is disabled, the
dispatcher does not read the outbox; it re-checks the config about every
30 seconds, so turning the feature on does not need a restart. Events that
happened while the feature was disabled are not backfilled.

An endpoint is validated whether or not the block is enabled. A disabled
block may omit the endpoint. A present endpoint that is not HTTPS, that
uses `http://` for a non-local host (including `HTTP://example.com`), or
that targets a private, link-local, or metadata address is invalid.
Decimal, hex, and octal spellings of an address (`2852039166`,
`0xa9fea9fe`) and a trailing dot (`metadata.google.internal.`) are the
same host. `http://` is allowed only for `localhost`, `127.0.0.1`, or
`::1`, and only when `allow_insecure_localhost` is true. Loopback HTTPS
stays allowed.

That private-host block applies to the delivery endpoint only.
`public_base_url` still has to be HTTPS (or localhost HTTP with the flag)
and must not embed credentials, but it may be a LAN origin such as
`https://192.168.1.10:8443`.

An invalid `manager_webhook` block does not stop the server. Boot logs
the error and leaves the feature disabled until the file parses. An
enabled block with a missing endpoint, or an unknown event name, is
invalid in the same way.

`extra_headers` cannot replace `Content-Type` or any `X-Omnigent-*` header.
`omnigent manager-webhook test` applies the same URL check before it
sends, so a plaintext probe of a foreign host is refused.

On an existing install the next server start adds three tables
(`session_lifecycle_outbox`, `session_lifecycle_cursors`,
`session_elicitations`) to `~/.omnigent/chat.db`. The migration only
creates tables and indexes.

### Probe the receiver

```bash
omnigent manager-webhook test
omnigent manager-webhook dead-letters
omnigent manager-webhook retry                  # every dead letter
omnigent manager-webhook retry --event-id <id>  # one row; repeatable
```

`test` posts one signed `manager_webhook.test` event. That type is not
stored and is not a lifecycle event. A non-2xx response exits 1.

`retry` moves dead-lettered rows back to pending and clears their attempt
count. A server that is already running claims them on its next poll
(within a couple of seconds). The command does not open a cross-process wake.

## Envelope

Every POST body is compact JSON (no insignificant whitespace). The bytes
you verify are the exact body bytes.

```json
{
  "event_id": "9f3c1a0e4b2d4c6a8e0f1a2b3c4d5e6f",
  "event_version": 1,
  "event_type": "session.awaiting_decision",
  "workspace_id": 0,
  "session_id": "0123456789abcdef0123456789abcdef",
  "sequence": 4,
  "occurred_at": 1760630400,
  "data": {}
}
```

| Field | Meaning |
| --- | --- |
| `event_id` | Stable id. Dedupe on this. The same transition always has the same id. |
| `event_version` | Envelope version. Currently `1`. |
| `event_type` | One of the names below, or `manager_webhook.test` for the CLI probe. |
| `workspace_id` | Tenant id. `0` on a single-user install. |
| `session_id` | Session the event is about. 32-char hex. |
| `sequence` | Monotonic per session. Delivered in order. |
| `occurred_at` | Unix seconds when the row was recorded. |
| `data` | Event-specific object. |

### Headers

| Header | Value |
| --- | --- |
| `Content-Type` | `application/json` |
| `X-Omnigent-Event-Id` | Same as `event_id` |
| `X-Omnigent-Event-Type` | Same as `event_type` |
| `X-Omnigent-Delivery-Attempt` | `1` on the first try, then 2, 3, … |
| `X-Omnigent-Timestamp` | Unix seconds used in the signature |
| `X-Omnigent-Signature` | `v1=<hex hmac-sha256>` |
| `X-Omnigent-Key-Id` | Present only when `key_id` is set |

A 2xx response acknowledges the delivery. Anything else, including a
redirect (redirects are not followed), is a failure and will be retried.

`omnigent.server.manager_webhook_signing.verify` and `previous_secret`
are the receiver-side helpers. The server signs with the current key
only. During rotation, a receiver checks the current secret and
`OMNIGENT_MANAGER_WEBHOOK_SECRET_PREVIOUS`.

## Signature

Signed content, UTF-8, is:

```text
{timestamp}.{event_id}.{raw_json_body}
```

`timestamp` is the `X-Omnigent-Timestamp` header. `raw_json_body` is the
raw request body, unchanged. The hex digest is lowercase. Compare with a
constant-time function. Reject timestamps more than 300 seconds from your
clock, even when the digest matches. During a rotation, accept the current
key or the previous key. The server only signs with the current key.

### Python

```python
import hashlib
import hmac
import time

TOLERANCE_SECONDS = 300


def verify(secret: str, body: bytes, headers: dict[str, str], now: int | None = None) -> bool:
    timestamp = int(headers["X-Omnigent-Timestamp"])
    if abs((now if now is not None else int(time.time())) - timestamp) > TOLERANCE_SECONDS:
        return False
    event_id = headers["X-Omnigent-Event-Id"]
    signed = f"{timestamp}.{event_id}.".encode() + body
    expected = "v1=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    signature = headers.get("X-Omnigent-Signature", "")
    if len(signature) != len(expected):
        return False
    return hmac.compare_digest(signature, expected)
```

### Node

`node:crypto` is built in. No packages.

```javascript
import { createHmac, timingSafeEqual } from "node:crypto";

const TOLERANCE_SECONDS = 300;

export function verify(secret, body, headers, now = Math.floor(Date.now() / 1000)) {
  const timestamp = Number(headers["x-omnigent-timestamp"]);
  if (!Number.isFinite(timestamp) || Math.abs(now - timestamp) > TOLERANCE_SECONDS) {
    return false;
  }
  const eventId = headers["x-omnigent-event-id"];
  const signed = `${timestamp}.${eventId}.${body.toString("utf8")}`;
  const expected = Buffer.from(
    "v1=" + createHmac("sha256", secret).update(signed, "utf8").digest("hex"),
  );
  const actual = Buffer.from(String(headers["x-omnigent-signature"] ?? ""));
  return expected.length === actual.length && timingSafeEqual(expected, actual);
}
```

Header names are lower-case here because Node's HTTP parser lower-cases
them. Match the casing your framework exposes.

## Idempotency, order, retries

Treat `event_id` as the idempotency key. A retry of the same transition
sends the same id and the same `sequence`. The attempt header increases.

Events for one session are delivered in `sequence` order. A later event
waits while an earlier one is still pending, leased, or dead-lettered.
That wait is intentional and lasts until the earlier row is delivered or
requeued with `retry`. A dead letter does not get skipped, so it can hold
later events for that session until you requeue it or the receiver starts
accepting it again.

Backoff is exponential with full jitter: about 5 seconds times 2^attempt,
capped at 15 minutes, then multiplied by a random factor between 0.5 and
1.0. `max_attempts` (default 10) is when the row is marked `dead_letter`.
Dead-letter is an escalation, not a drop: the row keeps retrying on that
same schedule, and it still blocks later events for that session. List
them with `omnigent manager-webhook dead-letters` and requeue with
`retry` when the receiver is healthy again.

A crash mid-delivery leaves the row leased. The lease expires after 60
seconds and the next process start claims it again. Inserts also wake the
live dispatcher immediately, so a healthy receiver sees
`session.awaiting_decision` well under a second. The two-second poll is
only the fallback for retries and for rows that were already pending at
startup.

## Events

### `session.awaiting_decision`

Emitted when an elicitation is published on the session that owns the
resolve route. Ancestor mirrors of a child elicitation are not recorded a
second time.

`data.request` is the upstream allowlist (`mode`, `message`,
`requestedSchema`, `url`, `phase`, `policy_name`, `target_session_id`,
`tool_name`). The fields beside it are this fork's richer context. Missing
values are omitted, not sent as null. `message`, `requestedSchema` string
values, and `url` are scanned for credentials before they are stored.
Schema property names are kept so the receiver can still render the form.

```json
{
  "event_id": "9f3c1a0e4b2d4c6a8e0f1a2b3c4d5e6f",
  "event_version": 1,
  "event_type": "session.awaiting_decision",
  "workspace_id": 0,
  "session_id": "0123456789abcdef0123456789abcdef",
  "sequence": 4,
  "occurred_at": 1760630400,
  "data": {
    "elicitation_id": "elicit_abc123",
    "created_at": 1760630400,
    "session_id": "0123456789abcdef0123456789abcdef",
    "target_session_id": "0123456789abcdef0123456789abcdef",
    "session_title": "Deploy the billing fix",
    "project_id": "89abcdef0123456789abcdef01234567",
    "project_name": "billing",
    "agent_name": "coder",
    "message": "Apply the migration?",
    "requestedSchema": {
      "type": "object",
      "properties": {"confirm": {"type": "boolean"}}
    },
    "resolve": {
      "method": "POST",
      "path": "/v1/sessions/0123456789abcdef0123456789abcdef/elicitations/elicit_abc123/resolve",
      "body": {"action": "accept", "content": {}},
      "actions": ["accept", "decline", "cancel"],
      "url": "https://omnigent.example.com/v1/sessions/0123456789abcdef0123456789abcdef/elicitations/elicit_abc123/resolve"
    },
    "web_url": "https://omnigent.example.com/approve/0123456789abcdef0123456789abcdef/elicit_abc123",
    "request": {
      "mode": "form",
      "message": "Apply the migration?",
      "requestedSchema": {
        "type": "object",
        "properties": {"confirm": {"type": "boolean"}}
      }
    }
  }
}
```

`target_session_id` is the child session when the elicitation belongs to a
sub-agent, and the session itself otherwise. The resolve path uses that
id. `web_url` and `resolve.url` are present only when `public_base_url`
is set. `web_url` opens the approval page
`{public_base_url}/approve/{session}/{elicitation_id}`.

### Answering

POST the resolve path from `data.resolve`. The body is:

```json
{"action": "accept", "content": {"confirm": true}}
```

`action` is `accept`, `decline`, or `cancel`. `content` is the object the
schema asked for; use `{}` when the question needs no fields. The route
responds `202 {"queued": false}` once the verdict is accepted. Authenticate
the same way any other `/v1` client does for that server.

### `session.decision_resolved` (fork extension)

Emitted when the human verdict is first stored, which can be before the
runner has consumed it. Use it to retract a stale prompt. Upstream builds
do not emit this event. `decision.content` is redacted the same way as a
summary: secret-shaped keys and credential-shaped strings become
`[redacted]`. `decided_by` on the ledger is the authenticated user when
the verdict arrived through the resolve route or an approval event.

```json
{
  "event_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "event_version": 1,
  "event_type": "session.decision_resolved",
  "workspace_id": 0,
  "session_id": "0123456789abcdef0123456789abcdef",
  "sequence": 5,
  "occurred_at": 1760630410,
  "data": {
    "elicitation_id": "elicit_abc123",
    "session_id": "0123456789abcdef0123456789abcdef",
    "target_session_id": "0123456789abcdef0123456789abcdef",
    "decision": {"action": "accept", "content": {"confirm": true}},
    "fork_extension": true
  }
}
```

### `session.resumed`

Emitted once the in-process harness consumed the verdict, or the runner
set `X-Omnigent-Pending-Approval-Resolved: 1` after
`pending_approvals.resolve()` succeeded. A 2xx from the forwarded
approval without that header is not consumption and does not emit this
event. If the runner already consumed the approval and then has no live
harness, it still sets that header on the 409, so the resume is recorded.

```json
{
  "event_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "event_version": 1,
  "event_type": "session.resumed",
  "workspace_id": 0,
  "session_id": "0123456789abcdef0123456789abcdef",
  "sequence": 6,
  "occurred_at": 1760630411,
  "data": {
    "elicitation_id": "elicit_abc123",
    "decision": {"action": "accept", "content": {"confirm": true}}
  }
}
```

### `session.completed`

Emitted when a turn goes idle. `summary` is a short assistant-message
excerpt, truncated to `summary_max_chars` (default 500). Credential-shaped
text is replaced with `[redacted]` before it is stored.

The completed and failed rows are written after the session status is
published. They are not in the same database transaction as the live
status row. If the outbox write fails, the server logs that at ERROR and
the status edge still goes out; that webhook event is not reconstructed
from the status row later.

```json
{
  "event_id": "cccccccccccccccccccccccccccccccc",
  "event_version": 1,
  "event_type": "session.completed",
  "workspace_id": 0,
  "session_id": "0123456789abcdef0123456789abcdef",
  "sequence": 7,
  "occurred_at": 1760630500,
  "data": {
    "response_id": "resp_123",
    "background_task_count": 0,
    "summary": "Migration applied on the billing branch."
  }
}
```

`background_task_count` and `summary` are omitted when they are empty.

### `session.failed`

`reason.message` is a fixed sentence chosen from `reason.code`. It is not
the raw error. `summary` is the truncated, redacted error text when one
was recorded.

A turn with a `response_id` uses one idempotent event
(`turn:{response_id}:failed`). A failure with no response id gets its own
event id. `data` omits `response_id` when the turn id is unknown.

One failure episode emits one event. Publishing `failed` again while the
session is already failed (the runner-offline sweep does this) does not
emit a second event. After the session is `running` again, the next
`failed` is a new episode and is delivered.

```json
{
  "event_id": "dddddddddddddddddddddddddddddddd",
  "event_version": 1,
  "event_type": "session.failed",
  "workspace_id": 0,
  "session_id": "0123456789abcdef0123456789abcdef",
  "sequence": 8,
  "occurred_at": 1760630600,
  "data": {
    "response_id": "resp_123",
    "reason": {
      "code": "runner_error",
      "message": "The runner reported an error."
    },
    "summary": "turn setup failed: [redacted]"
  }
}
```

Known `reason.code` values and the fixed `reason.message` that goes with
each:

| `reason.code` | `reason.message` |
| --- | --- |
| `runner_disconnected` | The runner disconnected unexpectedly. |
| `runner_failed_to_start` | The runner failed to start. |
| `runner_error` | The runner reported an error. |
| `runner_rejected_event` | The runner rejected an event for this turn. |
| `harness_not_configured` | The agent's harness is not configured on the selected host. |
| `codex_turn_error` | The turn failed on the harness side. |
| `codex_reauth_required` | Re-authentication is required for this session. |
| `unknown` | The session turn failed. |

Any other code uses "The session turn failed."

### `manager_webhook.test`

Sent only by `omnigent manager-webhook test`. `sequence` is 0 and
`session_id` is 32 zeroes.

```json
{
  "event_id": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
  "event_version": 1,
  "event_type": "manager_webhook.test",
  "workspace_id": 0,
  "session_id": "00000000000000000000000000000000",
  "sequence": 0,
  "occurred_at": 1760630700,
  "data": {"message": "omnigent manager-webhook test"}
}
```

## Delivery status

```http
GET /v1/sessions/{session_id}/manager-webhook-deliveries?limit=100&after={event_id}
```

The caller needs manage or owner access. A public-link viewer (read) is
rejected, and a user with no grant gets the same not-found response as
any other hidden session. The response is newest sequence first, includes
`last_error_message` for callers who passed the manage check, and does
not include the payload body:

```json
{
  "deliveries": [
    {
      "event_id": "9f3c1a0e4b2d4c6a8e0f1a2b3c4d5e6f",
      "event_type": "session.awaiting_decision",
      "sequence": 4,
      "status": "delivered",
      "attempt_count": 1,
      "last_attempt_at": 1760630400,
      "delivered_at": 1760630400,
      "last_http_status": 200,
      "last_error_code": null,
      "last_error_message": null
    }
  ],
  "next_cursor": null
}
```

`status` is `pending`, `leased`, `delivered`, `dead_letter`, or `paused`.
Pass `after` the previous page's `next_cursor` to read older rows.

Server logs one line per attempt on the `omnigent.server.manager_webhook_dispatcher`
logger. The line carries `event_id`, `event_type`, `session_id`,
`sequence`, `attempt_count`, `outcome` (`delivered`, `rejected`,
`transport_error`, `error`), `latency_ms`, `http_status`, `error_code`,
and `endpoint_host`. It does not include the body, the signature, or the
full URL.
