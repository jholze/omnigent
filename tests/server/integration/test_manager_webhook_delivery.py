"""Live HTTP delivery of the manager webhook.

A real socket receiver (not the ASGI transport) stands in for the bot.
The dispatcher is the production task: inserts wake it, and the idle poll
is only the fallback.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from omnigent.runtime import pending_elicitations
from omnigent.server import manager_webhook_dispatcher as dispatcher
from omnigent.server import manager_webhook_signing as signing
from omnigent.server import session_outbox
from omnigent.server.server_config import reset_manager_webhook_config_cache
from omnigent.server.session_outbox import SessionPushContext
from omnigent.stores.session_lifecycle_store.sqlalchemy_store import (
    SqlAlchemySessionLifecycleStore,
)

_SECRET = "test-secret-not-a-real-key"


class _Receiver(ThreadingHTTPServer):
    """Local webhook catcher. ``statuses`` overrides the response for the first N posts."""

    allow_reuse_address = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.events: list[dict[str, object]] = []
        self.lock = threading.Lock()
        self.statuses: list[int] = []
        self.delay_seconds = 0.0

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/hook"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        server: _Receiver = self.server  # type: ignore[assignment]
        if server.delay_seconds:
            time.sleep(server.delay_seconds)
        with server.lock:
            index = len(server.events)
            status = server.statuses[index] if index < len(server.statuses) else 200
            server.events.append(
                {
                    "body": body,
                    "headers": dict(self.headers.items()),
                    "status": status,
                    "received_at": time.monotonic(),
                }
            )
        payload = b"ok"
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture()
def receiver() -> Iterator[_Receiver]:
    server = _Receiver()
    thread = threading.Thread(target=server.serve_forever, name="webhook-receiver", daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _sid() -> str:
    return uuid.uuid4().hex


def _enable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    *,
    max_attempts: int = 10,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "manager_webhook:\n"
        "  enabled: true\n"
        f"  endpoint: {endpoint}\n"
        "  allow_insecure_localhost: true\n"
        "  public_base_url: http://127.0.0.1:8080\n"
        "  key_id: test-key\n"
        "  timeout_seconds: 2\n"
        f"  max_attempts: {max_attempts}\n"
        "  extra_headers:\n"
        "    X-Receiver-Route: grok-bot\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OMNIGENT_CONFIG", str(path))
    monkeypatch.setenv("OMNIGENT_MANAGER_WEBHOOK_SECRET", _SECRET)
    reset_manager_webhook_config_cache()


async def _wait_until_dispatcher_idle() -> None:
    for _ in range(100):
        if dispatcher._wake_event is not None:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("dispatcher did not bind its wake event")
    await asyncio.sleep(0.05)


async def _wait_until_delivered(
    store: SqlAlchemySessionLifecycleStore, session_id: str, *, timeout: float = 2.0
) -> None:
    """The receiver records the POST before the dispatcher commits ``delivered``."""
    deadline = time.monotonic() + timeout
    seen: set[str] = set()
    while time.monotonic() < deadline:
        rows, _cursor = store.list_deliveries(session_id)
        seen = {row.status for row in rows}
        if rows and seen == {"delivered"}:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"rows did not all reach delivered, saw {seen or 'none'}")


async def _wait_for(server: _Receiver, count: int, timeout: float) -> list[dict[str, object]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with server.lock:
            if len(server.events) >= count:
                return list(server.events)
        await asyncio.sleep(0.01)
    with server.lock:
        got = len(server.events)
    raise AssertionError(f"expected {count} deliveries, saw {got}")


def _payload(event: dict[str, object]) -> dict[str, object]:
    body = event["body"]
    assert isinstance(body, bytes)
    parsed = json.loads(body)
    assert isinstance(parsed, dict)
    return parsed


def _assert_signature(event: dict[str, object]) -> None:
    headers = event["headers"]
    body = event["body"]
    assert isinstance(headers, dict)
    assert isinstance(body, bytes)
    timestamp = int(headers["X-Omnigent-Timestamp"])
    assert signing.verify(
        signature_header=str(headers["X-Omnigent-Signature"]),
        timestamp=timestamp,
        event_id=str(headers["X-Omnigent-Event-Id"]),
        raw_json_body=body.decode("utf-8"),
        now=timestamp,
        secrets=[_SECRET],
    )
    assert headers["X-Receiver-Route"] == "grok-bot"
    assert headers["X-Omnigent-Key-Id"] == "test-key"


async def test_awaiting_decision_is_delivered_quickly_and_resolution_follows(
    db_uri: str,
    receiver: _Receiver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _enable(tmp_path, monkeypatch, receiver.url)
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_id = _sid()
    session_outbox.configure(
        store,
        context_lookup=lambda _session: SessionPushContext(
            title="Deploy the billing fix",
            project_id="proj",
            project_name="billing",
            agent_name="coder",
        ),
    )
    task = dispatcher.start_dispatcher(store)
    try:
        await _wait_until_dispatcher_idle()
        elicitation_id = "elicit_" + uuid.uuid4().hex
        started = time.monotonic()
        pending_elicitations.record_publish(
            session_id,
            {
                "type": "response.elicitation_request",
                "elicitation_id": elicitation_id,
                "tool_name": "shell",
                "params": {
                    "mode": "form",
                    "message": "Apply the migration?",
                    "requestedSchema": {"type": "object"},
                },
            },
        )
        events = await _wait_for(receiver, 1, 0.75)
        latency = float(events[0]["received_at"]) - started
        assert latency < 0.75
        body = _payload(events[0])
        assert body["event_type"] == "session.awaiting_decision"
        assert body["session_id"] == session_id
        data = body["data"]
        assert isinstance(data, dict)
        assert data["elicitation_id"] == elicitation_id
        assert data["session_title"] == "Deploy the billing fix"
        assert data["project_name"] == "billing"
        assert data["agent_name"] == "coder"
        assert data["message"] == "Apply the migration?"
        resolve = data["resolve"]
        assert isinstance(resolve, dict)
        assert resolve["path"] == (
            f"/v1/sessions/{session_id}/elicitations/{elicitation_id}/resolve"
        )
        assert resolve["body"] == {"action": "accept", "content": {}}
        assert data["web_url"] == f"http://127.0.0.1:8080/approve/{session_id}/{elicitation_id}"
        _assert_signature(events[0])

        pending_elicitations.record_publish(
            session_id,
            {
                "type": "response.elicitation_resolved",
                "elicitation_id": elicitation_id,
                "action": "accept",
                "content": {"confirm": True},
            },
        )
        session_outbox.record_elicitation_resumed(elicitation_id=elicitation_id)
        session_outbox.record_session_completed(
            session_id=session_id, response_id="resp-1", summary="Migration applied."
        )
        session_outbox.record_session_failed(
            session_id=session_id,
            response_id="resp-2",
            error_code="runner_error",
            error_message="disk full sk-supersecretvalue",
        )
        delivered = await _wait_for(receiver, 5, 2.0)
        kinds = [_payload(item)["event_type"] for item in delivered]
        assert kinds == [
            "session.awaiting_decision",
            "session.decision_resolved",
            "session.resumed",
            "session.completed",
            "session.failed",
        ]
        for item in delivered:
            _assert_signature(item)
        resolved = _payload(delivered[1])["data"]
        assert isinstance(resolved, dict)
        assert resolved["fork_extension"] is True
        assert resolved["decision"] == {"action": "accept", "content": {"confirm": True}}
        failed = _payload(delivered[4])["data"]
        assert isinstance(failed, dict)
        reason = failed["reason"]
        assert isinstance(reason, dict)
        assert reason["message"] == "The runner reported an error."
        assert "sk-supersecretvalue" not in json.dumps(failed)
        await _wait_until_delivered(store, session_id)
    finally:
        await dispatcher.cancel_dispatcher(task)


async def test_record_returns_before_a_slow_receiver(
    db_uri: str,
    receiver: _Receiver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receiver.delay_seconds = 0.4
    _enable(tmp_path, monkeypatch, receiver.url)
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_outbox.configure(store)
    task = dispatcher.start_dispatcher(store)
    try:
        await _wait_until_dispatcher_idle()
        started = time.monotonic()
        session_outbox.record_session_completed(
            session_id=_sid(), response_id="resp", summary="ok"
        )
        elapsed = time.monotonic() - started
        assert elapsed < 0.25
        await _wait_for(receiver, 1, 1.5)
    finally:
        await dispatcher.cancel_dispatcher(task)


async def test_retries_after_receiver_500s(
    db_uri: str,
    receiver: _Receiver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receiver.statuses = [500, 500]
    monkeypatch.setattr(dispatcher, "backoff_seconds", lambda _attempt: 0.0)
    _enable(tmp_path, monkeypatch, receiver.url, max_attempts=5)
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_outbox.configure(store)
    task = dispatcher.start_dispatcher(store)
    try:
        await _wait_until_dispatcher_idle()
        session_id = _sid()
        session_outbox.record_session_completed(session_id=session_id, response_id="resp")
        events = await _wait_for(receiver, 3, 2.0)
        assert [event["status"] for event in events] == [500, 500, 200]
        attempts = [int(event["headers"]["X-Omnigent-Delivery-Attempt"]) for event in events]  # type: ignore[index]
        assert attempts == [1, 2, 3]
        await _wait_until_delivered(store, session_id)
        latest = store.latest_delivery(session_id)
        assert latest is not None
        assert latest.attempt_count == 3
    finally:
        await dispatcher.cancel_dispatcher(task)


async def test_restart_delivers_rows_that_were_pending(
    db_uri: str,
    receiver: _Receiver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _enable(tmp_path, monkeypatch, receiver.url)
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_outbox.configure(store)
    session_id = _sid()
    session_outbox.record_session_completed(session_id=session_id, response_id="resp")
    assert dispatcher._wake_event is None
    assert store.latest_delivery(session_id).status == "pending"  # type: ignore[union-attr]
    restarted = SqlAlchemySessionLifecycleStore(db_uri)
    task = dispatcher.start_dispatcher(restarted)
    try:
        events = await _wait_for(receiver, 1, 1.0)
        assert _payload(events[0])["event_type"] == "session.completed"
        _assert_signature(events[0])
        await _wait_until_delivered(restarted, session_id)
    finally:
        await dispatcher.cancel_dispatcher(task)


async def test_disabled_dispatcher_sends_nothing(
    db_uri: str,
    receiver: _Receiver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OMNIGENT_CONFIG", raising=False)
    monkeypatch.delenv("OMNIGENT_MANAGER_WEBHOOK_SECRET", raising=False)
    reset_manager_webhook_config_cache()
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_outbox.configure(store)
    session_id = _sid()
    session_outbox.record_session_completed(session_id=session_id, response_id="resp")
    assert store.list_deliveries(session_id)[0] == []
    store.record_lifecycle_event(
        event_id=uuid.uuid4().hex,
        session_id=session_id,
        event_type="session.completed",
        transition_key="turn:manual:completed",
        payload='{"response_id":"manual"}',
        now=1,
    )
    task = dispatcher.start_dispatcher(store)
    try:
        await asyncio.sleep(0.4)
        with receiver.lock:
            assert receiver.events == []
        assert store.latest_delivery(session_id).status == "pending"  # type: ignore[union-attr]
    finally:
        await dispatcher.cancel_dispatcher(task)
