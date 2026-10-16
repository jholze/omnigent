"""Durable session-lifecycle push entities.

A :class:`LifecycleOutboxEvent` is one row of the transactional outbox that
records a session lifecycle event before it is delivered to the configured
manager webhook. A :class:`SessionElicitation` is the durable ledger row for
a human-decision request and its verdict.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LifecycleOutboxEvent:
    """One row of the ``session_lifecycle_outbox`` table.

    :param id: Stable ``event_id`` (deterministic UUIDv5, 32-char hex).
    :param session_id: Session this event reports on.
    :param event_type: ``session.completed`` / ``session.failed`` /
        ``session.awaiting_decision`` / ``session.resumed`` /
        ``session.decision_resolved``.
    :param transition_key: Stable source identity, e.g.
        ``"turn:{response_id}:completed"``.
    :param sequence: Monotonic per ``(workspace_id, session_id)``.
    :param event_version: Wire envelope version.
    :param payload: JSON event ``data`` (already redacted).
    :param status: ``pending`` / ``leased`` / ``delivered`` /
        ``dead_letter`` / ``paused``.
    :param attempt_count: Delivery attempts so far.
    :param next_attempt_at: Unix epoch seconds; the claim filter.
    :param created_at: Unix epoch seconds the row was inserted.
    :param workspace_id: Tenant partition key.
    """

    id: str
    session_id: str
    event_type: str
    transition_key: str
    sequence: int
    event_version: int
    payload: str
    status: str
    attempt_count: int
    next_attempt_at: int
    created_at: int
    workspace_id: int = 0
    lease_owner: str | None = None
    lease_expires_at: int | None = None
    last_attempt_at: int | None = None
    delivered_at: int | None = None
    last_http_status: int | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None


@dataclass
class SessionElicitation:
    """A durable elicitation ledger row in ``session_elicitations``.

    :param id: Elicitation id (a prefixed opaque token, not a bare UUID).
    :param session_id: Owning session.
    :param status: ``pending`` / ``decided`` / ``delivered_to_runner`` /
        ``expired``.
    :param request_payload: Allowlisted request fields, as JSON.
    :param created_at: Unix epoch seconds the row was created.
    """

    id: str
    session_id: str
    status: str
    request_payload: str
    created_at: int
    workspace_id: int = 0
    decision_payload: str | None = None
    decided_by: str | None = None
    decided_at: int | None = None
    resolved_at: int | None = None
