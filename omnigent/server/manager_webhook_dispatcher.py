"""Background delivery of the session-lifecycle outbox.

One task per server process. Inserts wake it through an ``asyncio.Event``
so ``session.awaiting_decision`` leaves the process well under a second.
The idle poll remains as the fallback for retries and for rows that were
pending when the process started.

HTTP delivery never runs on the request or agent path. A slow receiver
only occupies this task.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import socket
import time
from contextlib import suppress

import httpx

from omnigent.entities import LifecycleOutboxEvent
from omnigent.runtime.telemetry import _REDACT_KEY_SUBSTRINGS
from omnigent.server import manager_webhook_signing as signing
from omnigent.server.server_config import ManagerWebhookConfigError, manager_webhook_config
from omnigent.stores.session_lifecycle_store import SessionLifecycleStore

_logger = logging.getLogger(__name__)

_IDLE_POLL_SECONDS = 2.0
_CLAIM_BATCH_SIZE = 25
_LEASE_SECONDS = 60
_BACKOFF_BASE_SECONDS = 5.0
_BACKOFF_CAP_SECONDS = 15 * 60.0

_wake_event: asyncio.Event | None = None
_wake_loop: asyncio.AbstractEventLoop | None = None


def backoff_seconds(attempt: int) -> float:
    """Full-jitter exponential delay for a 1-based attempt count.

    The cap is 15 minutes. Dead-lettered rows keep using this schedule.
    """
    capped = min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2**attempt))
    return capped * random.uniform(0.5, 1.0)


def request_delivery() -> None:
    """Wake the dispatcher if it is running. Safe to call from any thread."""
    event = _wake_event
    loop = _wake_loop
    if event is None or loop is None:
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        event.set()
        return
    try:
        loop.call_soon_threadsafe(event.set)
    except RuntimeError:
        # Loop already closed. The next process start drains pending rows.
        return


def reset_dispatcher_for_tests() -> None:
    """Drop the wake handle so a finished test cannot signal a dead loop."""
    global _wake_event, _wake_loop
    _wake_event = None
    _wake_loop = None


def render_delivery_body(event: LifecycleOutboxEvent) -> str:
    """JSON body actually signed and transmitted for one outbox row."""
    return json.dumps(
        {
            "event_id": event.id,
            "event_version": event.event_version,
            "event_type": event.event_type,
            "workspace_id": event.workspace_id,
            "session_id": event.session_id,
            "sequence": event.sequence,
            "occurred_at": event.created_at,
            "data": json.loads(event.payload),
        },
        separators=(",", ":"),
    )


def _redact_for_log(value: str) -> str:
    lowered = value.lower()
    if any(token in lowered for token in _REDACT_KEY_SUBSTRINGS):
        return "[redacted]"
    return value


def _endpoint_host(endpoint: str) -> str:
    return httpx.URL(endpoint).host


def _log_attempt(
    event: LifecycleOutboxEvent,
    *,
    outcome: str,
    latency_ms: float,
    http_status: int | None,
    error_code: str | None,
    endpoint_host: str | None,
) -> None:
    """One structured line per attempt. Never includes the body, signature, or URL."""
    _logger.info(
        "manager_webhook delivery attempt",
        extra={
            "event_id": event.id,
            "workspace_id": event.workspace_id,
            "session_id": event.session_id,
            "sequence": event.sequence,
            "attempt_count": event.attempt_count,
            "event_type": event.event_type,
            "outcome": outcome,
            "latency_ms": round(latency_ms, 1),
            "http_status": http_status,
            "error_code": _redact_for_log(error_code) if error_code else None,
            "endpoint_host": endpoint_host,
        },
    )


def _fail(
    store: SessionLifecycleStore,
    event: LifecycleOutboxEvent,
    *,
    max_attempts: int,
    error_code: str,
    error_message: str,
    http_status: int | None = None,
) -> None:
    store.mark_delivery_failed(
        event.id,
        workspace_id=event.workspace_id,
        next_attempt_at=int(time.time()) + int(backoff_seconds(event.attempt_count)),
        dead_letter_after_attempts=max_attempts,
        http_status=http_status,
        error_code=error_code,
        error_message=error_message[:256],
    )


async def _deliver_one(
    client: httpx.AsyncClient,
    store: SessionLifecycleStore,
    event: LifecycleOutboxEvent,
    *,
    endpoint: str,
    key_id: str | None,
    timeout_seconds: float,
    max_attempts: int,
    extra_headers: dict[str, str],
) -> None:
    raw_json_body = render_delivery_body(event)
    secret = signing.current_signing_key()
    started = time.monotonic()
    host = _endpoint_host(endpoint)
    if secret is None:
        _fail(
            store,
            event,
            max_attempts=max_attempts,
            error_code="missing_signing_key",
            error_message=f"{signing.SECRET_ENV_VAR} is not set",
        )
        _log_attempt(
            event,
            outcome="error",
            latency_ms=0.0,
            http_status=None,
            error_code="missing_signing_key",
            endpoint_host=host,
        )
        return
    timestamp = int(time.time())
    signature = signing.sign(
        secret=secret,
        timestamp=timestamp,
        event_id=event.id,
        raw_json_body=raw_json_body,
    )
    headers = signing.build_headers(
        event_id=event.id,
        event_type=event.event_type,
        attempt=event.attempt_count,
        timestamp=timestamp,
        key_id=key_id,
        signature=signature,
    )
    for name, value in extra_headers.items():
        headers.setdefault(name, value)
    try:
        response = await client.post(
            endpoint,
            content=raw_json_body,
            headers=headers,
            timeout=timeout_seconds,
        )
    except httpx.HTTPError as exc:
        latency_ms = (time.monotonic() - started) * 1000
        _fail(
            store,
            event,
            max_attempts=max_attempts,
            error_code=type(exc).__name__,
            error_message=str(exc),
        )
        _log_attempt(
            event,
            outcome="transport_error",
            latency_ms=latency_ms,
            http_status=None,
            error_code=type(exc).__name__,
            endpoint_host=host,
        )
        return
    latency_ms = (time.monotonic() - started) * 1000
    if 200 <= response.status_code < 300:
        store.mark_delivered(
            event.id,
            workspace_id=event.workspace_id,
            delivered_at=int(time.time()),
            http_status=response.status_code,
        )
        _log_attempt(
            event,
            outcome="delivered",
            latency_ms=latency_ms,
            http_status=response.status_code,
            error_code=None,
            endpoint_host=host,
        )
        return
    _fail(
        store,
        event,
        max_attempts=max_attempts,
        http_status=response.status_code,
        error_code="http_error",
        error_message=f"manager returned {response.status_code}",
    )
    _log_attempt(
        event,
        outcome="rejected",
        latency_ms=latency_ms,
        http_status=response.status_code,
        error_code="http_error",
        endpoint_host=host,
    )


async def _run_once(
    client: httpx.AsyncClient,
    store: SessionLifecycleStore,
    *,
    lease_owner: str,
) -> int:
    """Claim and deliver. Returns 0 when disabled or idle."""
    try:
        config = manager_webhook_config()
    except ManagerWebhookConfigError:
        _logger.warning("manager_webhook config invalid; dispatcher idling", exc_info=True)
        return 0
    if not config.enabled or config.endpoint is None:
        return 0
    now = int(time.time())
    reclaimed = store.reclaim_expired_leases(now=now)
    if reclaimed:
        _logger.info("manager_webhook reclaimed %d expired lease(s)", reclaimed)
    claimed = store.claim_batch(
        limit=_CLAIM_BATCH_SIZE,
        now=now,
        lease_owner=lease_owner,
        lease_seconds=_LEASE_SECONDS,
    )
    extra = dict(config.extra_headers)
    for event in claimed:
        await _deliver_one(
            client,
            store,
            event,
            endpoint=config.endpoint,
            key_id=config.key_id,
            timeout_seconds=config.timeout_seconds,
            max_attempts=config.max_attempts,
            extra_headers=extra,
        )
    return len(claimed)


async def run_dispatcher(store: SessionLifecycleStore, *, lease_owner: str) -> None:
    """Run until cancelled. Binds the wake event to this task's loop."""
    global _wake_event, _wake_loop
    loop = asyncio.get_running_loop()
    _wake_loop = loop
    _wake_event = asyncio.Event()
    try:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            while True:
                try:
                    claimed_count = await _run_once(client, store, lease_owner=lease_owner)
                except Exception:  # one bad cycle must not kill the dispatcher
                    _logger.exception("manager_webhook dispatcher cycle failed")
                    claimed_count = 0
                if claimed_count:
                    continue
                event = _wake_event
                try:
                    await asyncio.wait_for(event.wait(), timeout=_IDLE_POLL_SECONDS)
                except TimeoutError:
                    pass
                else:
                    event.clear()
    finally:
        if _wake_loop is loop:
            reset_dispatcher_for_tests()


def start_dispatcher(store: SessionLifecycleStore) -> asyncio.Task[None]:
    """Start the process-local dispatcher task."""
    lease_owner = f"{socket.gethostname()}:{os.getpid()}"
    return asyncio.create_task(
        run_dispatcher(store, lease_owner=lease_owner),
        name="manager-webhook-dispatcher",
    )


async def cancel_dispatcher(task: asyncio.Task[None]) -> None:
    """Cancel the dispatcher and wait for it to finish."""
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
