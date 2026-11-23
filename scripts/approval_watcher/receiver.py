"""Local manager-webhook receiver that auto-approves harmless command cards.

Flow per POST: verify ``X-Omnigent-Signature`` → ignore non
``session.awaiting_decision`` events → run :mod:`policy` → resolve the
elicitation on the local server (approved) or leave it for the owner
(reported). Every decision is appended to a JSONL audit file.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from omnigent.server import manager_webhook_signing
from omnigent.server.session_outbox import redact_summary
from scripts.approval_watcher.policy import LOOPBACK_HOSTS, Decision, evaluate_card

_logger = logging.getLogger("approval_watcher")

AWAITING_DECISION = "session.awaiting_decision"

TOKEN_ENV_VAR = "OMNIGENT_APPROVAL_WATCHER_TOKEN"
COOKIE_ENV_VAR = "OMNIGENT_APPROVAL_WATCHER_COOKIE"
SERVER_URL_ENV_VAR = "OMNIGENT_APPROVAL_WATCHER_SERVER_URL"
ALLOW_REMOTE_SERVER_ENV_VAR = "OMNIGENT_APPROVAL_WATCHER_ALLOW_REMOTE_SERVER"
WORKSPACES_ENV_VAR = "OMNIGENT_APPROVAL_WATCHER_WORKSPACES"
AUDIT_PATH_ENV_VAR = "OMNIGENT_APPROVAL_WATCHER_AUDIT"
PORT_ENV_VAR = "OMNIGENT_APPROVAL_WATCHER_PORT"

DEFAULT_AUDIT_PATH = "/tmp/omnigent-approval-watcher.jsonl"
DEFAULT_PORT = 8787
_MAX_BODY_BYTES = 1_000_000
_AUDIT_COMMAND_MAX_CHARS = 2000
_RESOLVE_PATH_RE = re.compile(
    r"^/v1/sessions/[A-Za-z0-9_-]+/elicitations/(?P<elicitation>[A-Za-z0-9_-]+)/resolve$"
)


class ConfigError(ValueError):
    """Watcher configuration is missing or unsafe."""


def redact(text: str | None) -> str | None:
    """Credential-shaped substrings replaced, bounded for the audit line."""
    if text is None:
        return None
    return redact_summary(text, max_chars=_AUDIT_COMMAND_MAX_CHARS)


def validate_server_url(server_url: str, *, allow_remote: bool = False) -> str:
    """Return the origin without a trailing slash; loopback unless *allow_remote*."""
    parts = urlsplit(server_url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ConfigError("server URL must be http(s)://host[:port]")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ConfigError("server URL must not carry credentials, query, or fragment")
    if parts.path not in {"", "/"}:
        raise ConfigError("server URL must be an origin without a path")
    if not allow_remote and parts.hostname.lower() not in LOOPBACK_HOSTS:
        raise ConfigError("server URL host must be loopback unless explicitly allowed")
    return server_url.rstrip("/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Treat any 3xx as a failure instead of following it."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


@dataclass(frozen=True)
class ResolveResult:
    ok: bool
    detail: str


@dataclass
class HttpResolver:
    """POST the resolve body to ``{server_url}{path}`` with env-sourced auth."""

    server_url: str
    token: str | None = None
    cookie: str | None = None
    timeout_seconds: float = 10.0

    def __call__(self, path: str, body: dict[str, Any]) -> ResolveResult:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        elif self.cookie:
            headers["Cookie"] = self.cookie
        request = urllib.request.Request(
            f"{self.server_url}{path}",
            data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=self.timeout_seconds) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            return ResolveResult(False, f"resolve HTTP {error.code}")
        except (urllib.error.URLError, OSError) as error:
            return ResolveResult(False, f"resolve transport error: {type(error).__name__}")
        if 200 <= status < 300:
            return ResolveResult(True, f"resolve HTTP {status}")
        return ResolveResult(False, f"resolve HTTP {status}")


Resolver = Callable[[str, dict[str, Any]], ResolveResult]


class AuditLog:
    """Append-only JSONL file; one line per approved or reported card."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()

    def append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, separators=(",", ":"), sort_keys=True)
        with self._lock:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")


@dataclass(frozen=True)
class Outcome:
    """HTTP reply for one delivery plus the decision, when one was made."""

    status: int
    message: str
    decision: str | None = None


@dataclass
class Watcher:
    """Transport-free core; the HTTP handler and tests both call :meth:`handle`."""

    secrets: list[str]
    resolver: Resolver
    audit: AuditLog
    workspace_roots: Sequence[str] = ()
    clock: Callable[[], float] = time.time
    report: Callable[[str], None] = field(default=lambda line: print(line, flush=True))
    _seen: set[str] = field(default_factory=set)
    _seen_lock: threading.Lock = field(default_factory=threading.Lock)

    def handle(self, headers: dict[str, str], raw_body: bytes) -> Outcome:
        lowered = {key.lower(): value for key, value in headers.items()}
        try:
            timestamp = int(lowered.get("x-omnigent-timestamp", ""))
        except ValueError:
            return Outcome(401, "missing or invalid timestamp")
        event_id = lowered.get("x-omnigent-event-id", "")
        signature = lowered.get("x-omnigent-signature", "")
        try:
            body_text = raw_body.decode("utf-8")
        except UnicodeDecodeError:
            return Outcome(400, "body is not UTF-8")
        if (
            not event_id
            or not signature
            or not manager_webhook_signing.verify(
                signature_header=signature,
                timestamp=timestamp,
                event_id=event_id,
                raw_json_body=body_text,
                now=int(self.clock()),
                secrets=self.secrets,
            )
        ):
            return Outcome(401, "signature rejected")
        try:
            envelope = json.loads(body_text)
        except json.JSONDecodeError:
            return Outcome(400, "body is not JSON")
        if not isinstance(envelope, dict) or envelope.get("event_id") != event_id:
            return Outcome(400, "envelope event_id does not match header")
        if envelope.get("event_type") != AWAITING_DECISION:
            return Outcome(204, "event type ignored")
        with self._seen_lock:
            if event_id in self._seen:
                return Outcome(200, "duplicate event")
            self._seen.add(event_id)
        data = envelope.get("data")
        return self._decide(envelope, data if isinstance(data, dict) else {})

    def _decide(self, envelope: dict[str, Any], data: dict[str, Any]) -> Outcome:
        decision = evaluate_card(data, workspace_roots=self.workspace_roots)
        reason = decision.reason
        approved = False
        if decision.approve:
            target = _resolve_target(data)
            if isinstance(target, str):
                reason = f"allow-list passed but {target}"
            else:
                result = self.resolver(*target)
                approved = result.ok
                reason = decision.reason if result.ok else f"allow-list passed but {result.detail}"
        verdict = "approved" if approved else "reported"
        self._record(envelope, data, decision, verdict, reason)
        return Outcome(200, reason, verdict)

    def _record(
        self,
        envelope: dict[str, Any],
        data: dict[str, Any],
        decision: Decision,
        verdict: str,
        reason: str,
    ) -> None:
        now = self.clock()
        record = {
            "event_id": envelope.get("event_id"),
            "time": datetime.fromtimestamp(now, UTC).isoformat(),
            "epoch": round(now, 3),
            "occurred_at": envelope.get("occurred_at"),
            "session_id": envelope.get("session_id"),
            "target_session_id": data.get("target_session_id"),
            "elicitation_id": data.get("elicitation_id"),
            "policy_name": (data.get("request") or {}).get("policy_name")
            if isinstance(data.get("request"), dict)
            else None,
            "decision": verdict,
            "command": redact(decision.command),
            "reason": redact(reason),
        }
        self.audit.append(record)
        self.report(
            f"approval-watcher {verdict} session={record['session_id']} "
            f"elicitation={record['elicitation_id']} reason={record['reason']}"
        )


def _resolve_target(data: dict[str, Any]) -> tuple[str, dict[str, Any]] | str:
    """Return ``(path, body)`` from ``data.resolve``, or a reason string."""
    resolve = data.get("resolve")
    if not isinstance(resolve, dict):
        return "card has no resolve descriptor"
    path = resolve.get("path")
    match = _RESOLVE_PATH_RE.match(path) if isinstance(path, str) else None
    if match is None or match.group("elicitation") != data.get("elicitation_id"):
        return "resolve path is not this card's resolve route"
    if str(resolve.get("method", "POST")).upper() != "POST":
        return "resolve method is not POST"
    body = resolve.get("body")
    content = body.get("content") if isinstance(body, dict) else None
    return path, {"action": "accept", "content": content if isinstance(content, dict) else {}}


def _load_secrets() -> list[str]:
    current = manager_webhook_signing.current_signing_key()
    if current is None:
        raise ConfigError(f"{manager_webhook_signing.SECRET_ENV_VAR} is not set")
    previous = manager_webhook_signing.previous_secret()
    return [current, previous] if previous else [current]


def watcher_from_env(environ: dict[str, str] | None = None) -> Watcher:
    """Build a :class:`Watcher` from environment variables only."""
    env = os.environ if environ is None else environ
    server_url = env.get(SERVER_URL_ENV_VAR)
    if not server_url:
        raise ConfigError(f"{SERVER_URL_ENV_VAR} is required (e.g. http://127.0.0.1:<port>)")
    origin = validate_server_url(
        server_url, allow_remote=env.get(ALLOW_REMOTE_SERVER_ENV_VAR) == "1"
    )
    token = env.get(TOKEN_ENV_VAR) or None
    cookie = env.get(COOKIE_ENV_VAR) or None
    roots = [root for root in env.get(WORKSPACES_ENV_VAR, "").split(os.pathsep) if root]
    return Watcher(
        secrets=_load_secrets(),
        resolver=HttpResolver(origin, token=token, cookie=cookie),
        audit=AuditLog(env.get(AUDIT_PATH_ENV_VAR) or DEFAULT_AUDIT_PATH),
        workspace_roots=roots,
    )


def make_handler(watcher: Watcher) -> type[BaseHTTPRequestHandler]:
    """HTTP handler class bound to *watcher*."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "omnigent-approval-watcher"

        def do_POST(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0 or length > _MAX_BODY_BYTES:
                self._reply(Outcome(413, "body too large or missing length"))
                return
            raw_body = self.rfile.read(length)
            self._reply(watcher.handle(dict(self.headers.items()), raw_body))

        def _reply(self, outcome: Outcome) -> None:
            payload = json.dumps({"message": outcome.message, "decision": outcome.decision})
            encoded = payload.encode("utf-8") if outcome.status != 204 else b""
            self.send_response(outcome.status)
            if encoded:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            if encoded:
                self.wfile.write(encoded)

        def log_message(self, format: str, *args: Any) -> None:
            _logger.debug("%s " + format, self.address_string(), *args)

    return Handler


def serve(
    watcher: Watcher, *, host: str = "127.0.0.1", port: int = DEFAULT_PORT
) -> ThreadingHTTPServer:
    """Bind a loopback HTTP server for *watcher* (caller runs ``serve_forever``)."""
    if host.lower() not in LOOPBACK_HOSTS:
        raise ConfigError("receiver binds to loopback only")
    return ThreadingHTTPServer((host, port), make_handler(watcher))


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    try:
        watcher = watcher_from_env()
        port = int(os.environ.get(PORT_ENV_VAR) or DEFAULT_PORT)
        server = serve(watcher, port=port)
    except (ConfigError, ValueError, OSError) as error:
        print(f"approval-watcher: {error}", file=sys.stderr)
        return 2
    print(
        f"approval-watcher listening on http://127.0.0.1:{server.server_port}/ "
        f"audit={watcher.audit.path}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
