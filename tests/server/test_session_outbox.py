"""Payload building and the enabled-only outbox producer."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from omnigent.runtime import pending_elicitations
from omnigent.server import session_outbox
from omnigent.server.server_config import reset_manager_webhook_config_cache
from omnigent.server.session_outbox import SessionPushContext
from omnigent.stores.session_lifecycle_store.sqlalchemy_store import (
    SqlAlchemySessionLifecycleStore,
)


def _sid() -> str:
    return uuid.uuid4().hex


def _enable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, events: str | None = None) -> None:
    event_block = "" if events is None else f"\n  events: {events}"
    path = tmp_path / "config.yaml"
    path.write_text(
        "manager_webhook:\n"
        "  enabled: true\n"
        "  endpoint: https://bot.example.com/hook\n"
        "  public_base_url: https://omnigent.example.com\n"
        "  summary_max_chars: 40\n"
        f"{event_block}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OMNIGENT_CONFIG", str(path))
    monkeypatch.setenv("OMNIGENT_MANAGER_WEBHOOK_SECRET", "test-secret")
    reset_manager_webhook_config_cache()


def test_awaiting_decision_payload_is_self_contained() -> None:
    session_id = _sid()
    data = session_outbox.build_awaiting_decision_data(
        session_id=session_id,
        elicitation_id="elicit_abc",
        request={
            "mode": "form",
            "message": "Apply it?",
            "requestedSchema": {"type": "object"},
            "api_key": "should-not-leak",
            "target_session_id": "child-session",
            "tool_name": "shell",
        },
        created_at=100,
        context=SessionPushContext(
            title="Deploy",
            project_id="proj",
            project_name="billing",
            agent_name="coder",
        ),
        public_base_url="https://omnigent.example.com",
    )
    assert data["session_id"] == session_id
    assert data["target_session_id"] == "child-session"
    assert data["session_title"] == "Deploy"
    assert data["project_name"] == "billing"
    assert data["agent_name"] == "coder"
    assert data["message"] == "Apply it?"
    assert data["requestedSchema"] == {"type": "object"}
    assert "api_key" not in data["request"]
    assert data["resolve"]["method"] == "POST"
    assert data["resolve"]["path"] == "/v1/sessions/child-session/elicitations/elicit_abc/resolve"
    assert data["resolve"]["body"] == {"action": "accept", "content": {}}
    assert data["resolve"]["actions"] == ["accept", "decline", "cancel"]
    assert data["web_url"] == "https://omnigent.example.com/approve/child-session/elicit_abc"
    assert data["request"]["message"] == "Apply it?"


def test_failed_summary_is_redacted_and_reason_message_is_safe() -> None:
    data = session_outbox.build_failed_data(
        response_id="resp",
        error_code="runner_error",
        error_message=(
            "boom sk-supersecretvalue token=abc123 "
            'bearer of bad news {"api_key": "supersecretvalue"} '
            "Authorization: ya29.supersecretvalue "
            "Bearer ya29.a0AfH6SMBx1234567890"
        ),
        summary_max_chars=400,
    )
    assert data["reason"]["code"] == "runner_error"
    assert data["reason"]["message"] == "The runner reported an error."
    summary = data["summary"]
    assert "sk-supersecretvalue" not in summary
    assert "abc123" not in summary
    assert "supersecretvalue" not in summary
    assert "ya29.a0AfH6SMBx1234567890" not in summary
    assert "bearer of bad news" in summary
    assert "[redacted]" in summary
    unknown = session_outbox.build_failed_data(
        response_id="resp",
        error_code="custom_code",
        error_message="",
        summary_max_chars=80,
    )
    assert unknown["reason"]["message"] == "The session turn failed."
    assert "summary" not in unknown


def test_completed_omits_empty_summary() -> None:
    assert session_outbox.build_completed_data(
        response_id="resp", background_task_count=None, summary=None
    ) == {"response_id": "resp"}
    redacted = session_outbox.redact_summary("x" * 10, max_chars=4)
    assert redacted.endswith("…")
    assert len(redacted) == 4


def test_disabled_config_writes_nothing(
    db_uri: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OMNIGENT_CONFIG", raising=False)
    reset_manager_webhook_config_cache()
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_outbox.configure(store)
    session_id = _sid()
    session_outbox.record_session_completed(session_id=session_id, response_id="resp")
    session_outbox.record_elicitation_raised(
        session_id=session_id,
        elicitation_id="elicit_x",
        request={"message": "hi"},
    )
    assert store.list_deliveries(session_id)[0] == []
    assert store.get_elicitation("elicit_x") is None


def test_producer_inserts_once_and_wakes(
    db_uri: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(tmp_path, monkeypatch)
    store = SqlAlchemySessionLifecycleStore(db_uri)
    wakes: list[int] = []
    monkeypatch.setattr(session_outbox, "_notify", lambda: wakes.append(1))
    session_outbox.configure(
        store,
        context_lookup=lambda _sid: SessionPushContext(title="T", agent_name="coder"),
        summary_lookup=lambda _sid: "all done sk-supersecretvalue",
    )
    session_id = _sid()
    session_outbox.record_session_completed(session_id=session_id, response_id="resp-1")
    session_outbox.record_session_completed(session_id=session_id, response_id="resp-1")
    rows, _cursor = store.list_deliveries(session_id)
    assert len(rows) == 1
    assert rows[0].event_type == "session.completed"
    payload = json.loads(rows[0].payload)
    assert payload["response_id"] == "resp-1"
    assert "sk-supersecretvalue" not in payload["summary"]
    assert wakes == [1]

    session_outbox.record_session_failed(
        session_id=session_id,
        response_id="resp-1",
        error_code="runner_disconnected",
        error_message="socket closed",
    )
    failed = [
        row for row in store.list_deliveries(session_id)[0] if row.event_type == "session.failed"
    ]
    assert json.loads(failed[0].payload)["reason"]["message"] == (
        "The runner disconnected unexpectedly."
    )
    assert len(wakes) == 2


def test_unsubscribed_events_are_not_inserted(
    db_uri: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(tmp_path, monkeypatch, events="[session.completed]")
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_outbox.configure(store)
    session_id = _sid()
    session_outbox.record_session_failed(
        session_id=session_id,
        response_id="resp",
        error_code="unknown",
        error_message="nope",
    )
    assert store.list_deliveries(session_id)[0] == []


def test_listener_records_the_owner_and_does_not_replace_the_observer(
    db_uri: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(tmp_path, monkeypatch)
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_outbox.configure(store, context_lookup=lambda _sid: SessionPushContext(title="Ask"))
    seen: list[str] = []
    pending_elicitations.set_elicitation_observer(lambda _cid, event: seen.append(event["type"]))
    child = _sid()
    parent = _sid()
    elicitation_id = "elicit_" + uuid.uuid4().hex
    request = {
        "type": "response.elicitation_request",
        "elicitation_id": elicitation_id,
        "params": {"mode": "form", "message": "Continue?", "requestedSchema": {"type": "object"}},
    }
    pending_elicitations.record_publish(
        parent,
        {**request, "params": {**request["params"], "target_session_id": child}},
    )
    assert store.get_elicitation(elicitation_id) is None
    pending_elicitations.record_publish(child, request)
    row = store.get_elicitation(elicitation_id)
    assert row is not None
    assert row.session_id == child
    assert row.status == "pending"
    pending_elicitations.record_publish(child, request)
    assert len(store.list_deliveries(child)[0]) == 1
    pending_elicitations.record_publish(
        child,
        {
            "type": "response.elicitation_resolved",
            "elicitation_id": elicitation_id,
            "action": "decline",
        },
    )
    decided = store.get_elicitation(elicitation_id)
    assert decided is not None
    assert decided.status == "decided"
    types = [item.event_type for item in store.list_deliveries(child)[0]]
    assert "session.awaiting_decision" in types
    assert "session.decision_resolved" in types
    resolved = json.loads(
        next(
            item.payload
            for item in store.list_deliveries(child)[0]
            if item.event_type == "session.decision_resolved"
        )
    )
    assert resolved["fork_extension"] is True
    assert resolved["decision"]["action"] == "decline"
    assert seen == [
        "response.elicitation_request",
        "response.elicitation_request",
        "response.elicitation_request",
        "response.elicitation_resolved",
    ]
    pending_elicitations.set_elicitation_observer(None)


def test_ownership_mismatch_does_not_overwrite(
    db_uri: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(tmp_path, monkeypatch)
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_outbox.configure(store)
    owner = _sid()
    other = _sid()
    elicitation_id = "elicit_" + uuid.uuid4().hex
    session_outbox.record_elicitation_raised(
        session_id=owner,
        elicitation_id=elicitation_id,
        request={"message": "go?"},
    )
    mismatch = session_outbox.record_decision(
        elicitation_id=elicitation_id,
        session_id=other,
        decision={"action": "accept"},
    )
    assert mismatch.ownership_mismatch is True
    assert store.get_elicitation(elicitation_id).status == "pending"  # type: ignore[union-attr]


def test_publish_status_records_terminal_edges_and_skips_swallowed_idle(
    db_uri: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(tmp_path, monkeypatch)
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_outbox.configure(store, summary_lookup=lambda _sid: "finished")
    from omnigent.server.routes._sessions import helpers as session_helpers
    from omnigent.server.schemas import ErrorDetail

    session_id = _sid()
    session_helpers._publish_status(session_id, "running", response_id="resp-9")
    session_helpers._publish_status(session_id, "idle")
    completed = store.list_deliveries(session_id)[0]
    assert len(completed) == 1
    assert json.loads(completed[0].payload)["response_id"] == "resp-9"
    assert json.loads(completed[0].payload)["summary"] == "finished"

    failed_id = _sid()
    session_helpers._publish_status(
        failed_id,
        "failed",
        error=ErrorDetail(code="runner_error", message="disk full sk-supersecretvalue"),
        response_id="resp-fail",
    )
    failed = json.loads(store.list_deliveries(failed_id)[0][0].payload)
    assert failed["reason"]["message"] == "The runner reported an error."
    assert "sk-supersecretvalue" not in failed["summary"]

    sticky = _sid()
    session_helpers._session_status_cache[sticky] = "failed"
    session_helpers._publish_status(sticky, "idle", response_id="should-not-record")
    assert store.list_deliveries(sticky)[0] == []


def test_two_failures_without_a_response_id_both_persist(
    db_uri: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(tmp_path, monkeypatch)
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_outbox.configure(store)
    session_id = _sid()
    session_outbox.record_session_failed(
        session_id=session_id,
        response_id=None,
        error_code="runner_error",
        error_message="first boom",
    )
    session_outbox.record_session_failed(
        session_id=session_id,
        response_id=None,
        error_code="runner_error",
        error_message="second boom",
    )
    session_outbox.record_session_failed(
        session_id=session_id,
        response_id="resp-same",
        error_code="runner_error",
        error_message="same turn",
    )
    session_outbox.record_session_failed(
        session_id=session_id,
        response_id="resp-same",
        error_code="runner_error",
        error_message="same turn again",
    )
    failed = [
        row for row in store.list_deliveries(session_id)[0] if row.event_type == "session.failed"
    ]
    assert len(failed) == 3
    untitled = [row for row in failed if row.transition_key.startswith("failure:")]
    assert len(untitled) == 2
    assert len({row.transition_key for row in untitled}) == 2
    payloads = [json.loads(row.payload) for row in untitled]
    assert {payload["summary"] for payload in payloads} == {"first boom", "second boom"}
    assert all("response_id" not in payload for payload in payloads)
    keyed = [row for row in failed if row.transition_key == "turn:resp-same:failed"]
    assert len(keyed) == 1


def test_awaiting_decision_and_verdict_redact_free_text(
    db_uri: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(tmp_path, monkeypatch)
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_outbox.configure(store)
    session_id = _sid()
    elicitation_id = "elicit_" + uuid.uuid4().hex
    session_outbox.record_elicitation_raised(
        session_id=session_id,
        elicitation_id=elicitation_id,
        request={
            "mode": "form",
            "message": 'paste {"api_key": "supersecretvalue"} bearer of bad news',
            "requestedSchema": {
                "type": "object",
                "properties": {
                    "api_key": {"type": "string", "description": "bearer of bad news"},
                },
            },
            "url": "https://example.com/hook?token=abc123",
        },
    )
    row = store.get_elicitation(elicitation_id)
    assert row is not None
    stored = json.loads(row.request_payload)
    assert "supersecretvalue" not in json.dumps(stored)
    assert "abc123" not in stored["url"]
    assert "bearer of bad news" in stored["message"]
    schema = stored["requestedSchema"]["properties"]["api_key"]
    assert schema["type"] == "string"
    assert schema["description"] == "bearer of bad news"
    event = json.loads(store.list_deliveries(session_id)[0][0].payload)
    assert "supersecretvalue" not in json.dumps(event)
    assert event["message"].startswith("paste ")

    written = session_outbox.record_decision(
        elicitation_id=elicitation_id,
        session_id=session_id,
        decision={
            "action": "accept",
            "content": {
                "api_key": "supersecretvalue",
                "note": "Authorization: ya29.supersecretvalue",
                "aside": "bearer of bad news",
            },
        },
        decided_by="owner@example.com",
    )
    assert written.elicitation is not None
    assert written.elicitation.decided_by == "owner@example.com"
    content = json.loads(written.elicitation.decision_payload or "{}")["content"]
    assert content["api_key"] == "[redacted]"
    assert "ya29.supersecretvalue" not in content["note"]
    assert content["aside"] == "bearer of bad news"


async def test_runner_2xx_without_resolved_header_does_not_resume(
    db_uri: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import httpx

    from omnigent.server.routes._sessions import orchestration

    _enable(tmp_path, monkeypatch)
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_outbox.configure(store)
    session_id = _sid()
    elicitation_id = "elicit_" + uuid.uuid4().hex
    session_outbox.record_elicitation_raised(
        session_id=session_id,
        elicitation_id=elicitation_id,
        request={"message": "Continue?"},
    )

    async def _forward_plain(_session_id: str, _data: dict, _router: object) -> httpx.Response:
        return httpx.Response(200)

    monkeypatch.setattr(orchestration, "_forward_approval_to_runner", _forward_plain)
    await orchestration._resolve_elicitation(
        session_id,
        {"elicitation_id": elicitation_id, "action": "accept"},
        runner_router=None,
        decided_by="owner@example.com",
    )
    types = [row.event_type for row in store.list_deliveries(session_id)[0]]
    assert "session.resumed" not in types
    decided = store.get_elicitation(elicitation_id)
    assert decided is not None
    assert decided.status == "decided"
    assert decided.decided_by == "owner@example.com"

    async def _forward_consumed(_session_id: str, _data: dict, _router: object) -> httpx.Response:
        return httpx.Response(200, headers={"X-Omnigent-Pending-Approval-Resolved": "1"})

    monkeypatch.setattr(orchestration, "_forward_approval_to_runner", _forward_consumed)
    await orchestration._resolve_elicitation(
        session_id,
        {"elicitation_id": elicitation_id, "action": "accept"},
        runner_router=None,
        decided_by="owner@example.com",
    )
    types = [row.event_type for row in store.list_deliveries(session_id)[0]]
    assert "session.resumed" in types
