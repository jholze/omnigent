"""SQLite persistence for the session-lifecycle outbox and elicitation ledger."""

from __future__ import annotations

import uuid

from omnigent.stores.session_lifecycle_store.sqlalchemy_store import (
    SqlAlchemySessionLifecycleStore,
)


def _sid() -> str:
    return uuid.uuid4().hex


def _event_id() -> str:
    return uuid.uuid4().hex


def _record(
    store: SqlAlchemySessionLifecycleStore,
    session_id: str,
    *,
    key: str,
    event_type: str = "session.completed",
    now: int = 1_000,
) -> tuple[object, bool]:
    return store.record_lifecycle_event(
        event_id=_event_id(),
        session_id=session_id,
        event_type=event_type,
        transition_key=key,
        payload='{"response_id":"r"}',
        now=now,
    )


def test_sequence_is_monotonic_and_transitions_are_idempotent(db_uri: str) -> None:
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_id = _sid()
    first, inserted = _record(store, session_id, key="turn:a:completed", now=10)
    again, again_inserted = store.record_lifecycle_event(
        event_id=_event_id(),
        session_id=session_id,
        event_type="session.completed",
        transition_key="turn:a:completed",
        payload='{"response_id":"other"}',
        now=11,
    )
    second, _ = _record(store, session_id, key="turn:b:completed", now=12)
    assert inserted is True
    assert again_inserted is False
    assert again.id == first.id  # type: ignore[attr-defined]
    assert first.sequence == 1  # type: ignore[attr-defined]
    assert second.sequence == 2  # type: ignore[attr-defined]
    assert first.payload == '{"response_id":"r"}'  # type: ignore[attr-defined]


def test_claim_preserves_order_and_dead_letter_blocks_later_rows(db_uri: str) -> None:
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_id = _sid()
    first, _ = _record(store, session_id, key="turn:1:completed", now=100)
    _record(store, session_id, key="turn:2:completed", now=100)
    claimed = store.claim_batch(limit=10, now=100, lease_owner="host:1", lease_seconds=30)
    assert [row.id for row in claimed] == [first.id]  # type: ignore[attr-defined]
    assert claimed[0].status == "leased"
    assert claimed[0].attempt_count == 1
    store.mark_delivery_failed(
        first.id,  # type: ignore[attr-defined]
        workspace_id=first.workspace_id,  # type: ignore[attr-defined]
        next_attempt_at=500,
        dead_letter_after_attempts=1,
        http_status=500,
        error_code="http_error",
        error_message="manager returned 500",
    )
    blocked = store.claim_batch(limit=10, now=200, lease_owner="host:1", lease_seconds=30)
    assert blocked == []
    retried = store.claim_batch(limit=10, now=500, lease_owner="host:1", lease_seconds=30)
    assert [row.id for row in retried] == [first.id]  # type: ignore[attr-defined]
    assert retried[0].status == "leased"
    store.mark_delivered(
        first.id,  # type: ignore[attr-defined]
        workspace_id=first.workspace_id,  # type: ignore[attr-defined]
        delivered_at=501,
        http_status=200,
    )
    nxt = store.claim_batch(limit=10, now=500, lease_owner="host:1", lease_seconds=30)
    assert len(nxt) == 1
    assert nxt[0].sequence == 2


def test_stale_lease_owner_cannot_mark_delivered_or_failed(db_uri: str) -> None:
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_id = _sid()
    row, _ = _record(store, session_id, key="turn:1:completed", now=1)
    claimed = store.claim_batch(limit=1, now=1, lease_owner="host:1", lease_seconds=30)
    assert claimed[0].lease_owner == "host:1"
    store.mark_delivered(
        row.id,  # type: ignore[attr-defined]
        workspace_id=row.workspace_id,  # type: ignore[attr-defined]
        delivered_at=2,
        http_status=200,
        lease_owner="host:stale",
    )
    assert store.latest_delivery(session_id).status == "leased"  # type: ignore[union-attr]
    store.mark_delivery_failed(
        row.id,  # type: ignore[attr-defined]
        workspace_id=row.workspace_id,  # type: ignore[attr-defined]
        next_attempt_at=9,
        dead_letter_after_attempts=1,
        error_code="http_error",
        error_message="stale",
        lease_owner="host:stale",
    )
    assert store.latest_delivery(session_id).status == "leased"  # type: ignore[union-attr]
    store.mark_delivered(
        row.id,  # type: ignore[attr-defined]
        workspace_id=row.workspace_id,  # type: ignore[attr-defined]
        delivered_at=3,
        http_status=200,
        lease_owner="host:1",
    )
    assert store.latest_delivery(session_id).status == "delivered"  # type: ignore[union-attr]


def test_lease_reclaim_and_requeue(db_uri: str) -> None:
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_id = _sid()
    row, _ = _record(store, session_id, key="turn:1:failed", event_type="session.failed", now=1)
    store.claim_batch(limit=5, now=1, lease_owner="host:1", lease_seconds=10)
    assert store.reclaim_expired_leases(now=5) == 0
    assert store.reclaim_expired_leases(now=12) == 1
    reclaimed = store.latest_delivery(session_id)
    assert reclaimed is not None
    assert reclaimed.status == "pending"
    store.claim_batch(limit=5, now=12, lease_owner="host:1", lease_seconds=10)
    store.mark_delivery_failed(
        row.id,  # type: ignore[attr-defined]
        workspace_id=row.workspace_id,  # type: ignore[attr-defined]
        next_attempt_at=99,
        dead_letter_after_attempts=1,
        error_code="http_error",
        error_message="nope",
    )
    dead = store.list_by_status("dead_letter")
    assert [item.id for item in dead] == [row.id]  # type: ignore[attr-defined]
    assert store.requeue_dead_letters([row.id], now=20) == 1  # type: ignore[attr-defined]
    pending = store.latest_delivery(session_id)
    assert pending is not None
    assert pending.status == "pending"
    assert pending.attempt_count == 0
    assert pending.last_error_code is None
    assert store.requeue_dead_letters(None, now=21) == 0


def test_elicitation_ledger_decision_and_resume(db_uri: str) -> None:
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_id = _sid()
    elicitation_id = "elicit_" + uuid.uuid4().hex
    row, event, inserted = store.record_elicitation_raised(
        elicitation_id=elicitation_id,
        session_id=session_id,
        request_payload='{"message":"go?"}',
        outbox_event_id=_event_id(),
        transition_key=f"elicitation:{elicitation_id}:awaiting_decision",
        outbox_payload='{"elicitation_id":"x"}',
        now=10,
    )
    assert inserted is True
    assert row.status == "pending"
    assert event is not None and event.event_type == "session.awaiting_decision"
    again, _event, again_inserted = store.record_elicitation_raised(
        elicitation_id=elicitation_id,
        session_id=session_id,
        request_payload="{}",
        outbox_event_id=_event_id(),
        transition_key=f"elicitation:{elicitation_id}:awaiting_decision",
        outbox_payload="{}",
        now=11,
    )
    assert again_inserted is False
    assert again.status == "pending"

    decided = store.record_decision(
        elicitation_id,
        decision_payload='{"action":"accept"}',
        decided_by="user",
        now=20,
        outbox_event_id=_event_id(),
        outbox_transition_key=f"elicitation:{elicitation_id}:decision_resolved",
        outbox_payload='{"fork_extension":true}',
    )
    assert decided is not None
    assert decided.status == "decided"
    assert decided.decision_payload == '{"action":"accept"}'
    second = store.record_decision(
        elicitation_id,
        decision_payload='{"action":"decline"}',
        decided_by="other",
        now=21,
    )
    assert second is not None
    assert second.decision_payload == '{"action":"accept"}'
    types = [item.event_type for item in store.list_deliveries(session_id)[0]]
    assert types.count("session.decision_resolved") == 1

    resumed, resumed_event, resumed_inserted = store.record_elicitation_resolved(
        elicitation_id,
        outbox_event_id=_event_id(),
        transition_key=f"elicitation:{elicitation_id}:resumed",
        outbox_payload='{"elicitation_id":"x"}',
        resolved_at=30,
    )
    assert resumed_inserted is True
    assert resumed is not None and resumed.status == "delivered_to_runner"
    assert resumed_event is not None and resumed_event.event_type == "session.resumed"
    _row, _event, third = store.record_elicitation_resolved(
        elicitation_id,
        outbox_event_id=_event_id(),
        transition_key=f"elicitation:{elicitation_id}:resumed",
        outbox_payload="{}",
        resolved_at=31,
    )
    assert third is False
    undelivered = store.list_decided_undelivered(session_id)
    assert undelivered == []


def test_list_deliveries_pages_newest_first(db_uri: str) -> None:
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_id = _sid()
    for index in range(3):
        _record(store, session_id, key=f"turn:{index}:completed", now=index)
    page, cursor = store.list_deliveries(session_id, limit=2)
    assert [row.sequence for row in page] == [3, 2]
    assert cursor == page[-1].id
    rest, next_cursor = store.list_deliveries(session_id, limit=2, after_id=cursor)
    assert [row.sequence for row in rest] == [1]
    assert next_cursor is None
