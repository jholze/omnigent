"""Backoff math and the structured delivery log."""

from __future__ import annotations

import logging
import uuid

from omnigent.entities import LifecycleOutboxEvent
from omnigent.server.manager_webhook_dispatcher import (
    _BACKOFF_BASE_SECONDS,
    _BACKOFF_CAP_SECONDS,
    _log_attempt,
    backoff_seconds,
)


def test_backoff_is_full_jitter_and_capped() -> None:
    for attempt in range(12):
        cap = min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2**attempt))
        samples = [backoff_seconds(attempt) for _ in range(30)]
        assert min(samples) >= cap * 0.5
        assert max(samples) <= cap
        assert min(samples) < max(samples)


def test_delivery_log_has_no_body_or_signature(caplog: object) -> None:
    event = LifecycleOutboxEvent(
        id=uuid.uuid4().hex,
        session_id="ab" * 16,
        event_type="session.failed",
        transition_key="turn:1:failed",
        sequence=3,
        event_version=1,
        payload='{"summary":"secret=hunter2"}',
        status="leased",
        attempt_count=2,
        next_attempt_at=1,
        created_at=1,
        workspace_id=0,
    )
    caplog.set_level(logging.INFO, logger="omnigent.server.manager_webhook_dispatcher")  # type: ignore[attr-defined]
    _log_attempt(
        event,
        outcome="rejected",
        latency_ms=12.4,
        http_status=500,
        error_code="http_error",
        endpoint_host="bot.example.com",
    )
    _log_attempt(
        event,
        outcome="error",
        latency_ms=0,
        http_status=None,
        error_code="contains_secret_value",
        endpoint_host="bot.example.com",
    )
    records = caplog.records  # type: ignore[attr-defined]
    assert len(records) == 2
    rendered = " ".join(record.getMessage() for record in records)
    assert "hunter2" not in rendered
    assert "v1=" not in rendered
    assert records[0].outcome == "rejected"
    assert records[0].endpoint_host == "bot.example.com"
    assert records[0].event_id == event.id
    assert records[1].error_code == "[redacted]"
