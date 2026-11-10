"""Stall detection, delivery acks, and idempotent event POST.

Each test maps to one acceptance criterion from the session-wake spec.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from omnigent.runtime import _globals as runtime_globals
from omnigent.runtime import session_stream, stall_progress
from omnigent.runtime.stall_progress import Clock, Decision, StallSettings, evaluate
from omnigent.server import event_idempotency, session_outbox, stall_watchdog
from omnigent.server.event_idempotency import (
    IdempotencyConflict,
    resolve_idempotent,
    retry_same_key,
)
from omnigent.server.mcp_endpoint import ToolFailure, _Api
from omnigent.server.server_config import reset_manager_webhook_config_cache
from omnigent.server.stall_watchdog import (
    annotate_message_ack,
    apply_effects,
    evaluate_session,
    install_label_writer,
    install_publisher,
    load_settings,
    register_interrupt_callback,
)
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.session_lifecycle_store.sqlalchemy_store import (
    SqlAlchemySessionLifecycleStore,
)

_ROOT = Path(__file__).resolve().parents[2]


# 32-char hex. A ConversationStore rejects anything shorter.
_SID_AC3 = "0000000000000000000000000000ac03"
_SID_AC4 = "0000000000000000000000000000ac04"
_SID_AC4B = "00000000000000000000000000ac004b"
_SID_AC4_CN = "0000000000000000000000000000a4c1"
_SID_AC5 = "0000000000000000000000000000ac05"
_SID_DRAIN = "0000000000000000000000000000d7a1"


@pytest.fixture(autouse=True)
def _isolate_stall_state():
    previous_publisher = stall_watchdog._publisher
    previous_writer = stall_watchdog._label_writer
    previous_interrupt = stall_watchdog._interrupt
    # Server tests leave a ConversationStore installed. These cases must
    # not inherit it; a short id would abort get_conversation.
    previous_store = runtime_globals._conversation_store
    runtime_globals._conversation_store = None
    stall_progress.reset_for_tests()
    event_idempotency.reset_for_tests()
    yield
    stall_progress.reset_for_tests()
    event_idempotency.reset_for_tests()
    stall_watchdog._publisher = previous_publisher
    stall_watchdog._label_writer = previous_writer
    stall_watchdog._interrupt = previous_interrupt
    runtime_globals._conversation_store = previous_store


def _ack(session_id: str, *, status: str, now: float, relaunched: bool = False) -> dict:
    return annotate_message_ack(
        session_id,
        {"queued": True, "item_id": "item_1"},
        status_before=status,
        relaunched=relaunched,
        now=now,
    )


def test_ac1_idle_send_starts_a_turn() -> None:
    ack = _ack("conv_ac1", status="idle", now=0.0)
    assert ack["delivery"] == "turn_started"
    assert ack["item_id"] == "item_1"
    relaunched = annotate_message_ack(
        "conv_ac1_relaunch",
        {"queued": True, "item_id": "item_r"},
        status_before="idle",
        relaunched=True,
        now=0.0,
    )
    assert relaunched["delivery"] == "runner_restarted"
    assert relaunched["item_id"] == "item_r"


def test_ac2_running_send_is_buffered_with_turn_age() -> None:
    _ack("conv_ac2", status="idle", now=0.0)
    ack = _ack("conv_ac2", status="running", now=12.0)
    assert ack["delivery"] == "buffered_behind_active_turn"
    assert ack["active_turn_age_s"] == 12.0
    assert ack["seconds_since_last_event"] == 12.0
    assert ack["item_id"] == "item_1"
    clock = stall_progress.get_clock("conv_ac2")
    assert clock is not None
    assert clock.last_event_at == 0.0
    assert clock.buffered_count == 1


async def test_ac3_watchdog_marks_and_emits_once() -> None:
    events: list[dict] = []
    labels: list[tuple[str, str | None]] = []
    uninstall_publisher = install_publisher(lambda sid, event: events.append(event))
    uninstall_writer = install_label_writer(lambda sid, value: labels.append((sid, value)))
    try:
        _ack(_SID_AC3, status="idle", now=0.0)
        first = await evaluate_session(_SID_AC3, now=300.0, settings=StallSettings())
        second = await evaluate_session(_SID_AC3, now=301.0, settings=StallSettings())
    finally:
        uninstall_publisher()
        uninstall_writer()
    assert first.mark_stalled is True
    assert first.emit_stalled is True
    assert second.emit_stalled is False
    assert [event["type"] for event in events] == ["session.stalled"]
    assert labels == [(_SID_AC3, "stalled")]
    clock = stall_progress.get_clock(_SID_AC3)
    assert clock is not None and clock.stalled is True


async def test_ac4_buffered_silence_interrupts_once_and_starts_next_turn() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def _interrupt(session_id: str) -> None:
        calls.append(session_id)
        started.set()
        await release.wait()

    uninstall = register_interrupt_callback(_interrupt)
    try:
        _ack(_SID_AC4, status="idle", now=0.0)
        _ack(_SID_AC4, status="running", now=700.0)
        task = asyncio.create_task(evaluate_session(_SID_AC4, now=700.0, settings=StallSettings()))
        await asyncio.wait_for(started.wait(), timeout=2)
        clock = stall_progress.get_clock(_SID_AC4)
        assert clock is not None
        assert clock.last_auto_interrupt_at == 700.0
        during = await evaluate_session(_SID_AC4, now=701.0, settings=StallSettings())
        assert during.interrupt is False
        release.set()
        decision = await task
    finally:
        uninstall()
    assert decision.interrupt is True
    assert decision.buffered_starts_next_turn is True
    assert calls == [_SID_AC4]


async def test_ac4_buffer_between_thresholds_marks_without_interrupt() -> None:
    calls: list[str] = []

    async def _interrupt(session_id: str) -> None:
        calls.append(session_id)

    uninstall = register_interrupt_callback(_interrupt)
    try:
        _ack(_SID_AC4B, status="idle", now=0.0)
        _ack(_SID_AC4B, status="running", now=450.0)
        decision = await evaluate_session(_SID_AC4B, now=450.0, settings=StallSettings())
    finally:
        uninstall()
    assert decision.mark_stalled is True
    assert decision.interrupt is False
    assert decision.buffered_starts_next_turn is False
    assert calls == []


async def test_ac4_claude_native_marks_without_interrupt() -> None:
    """Auto-interrupt would drop claude-native's runner buffer (FR-3)."""
    calls: list[str] = []

    async def _interrupt(session_id: str) -> None:
        calls.append(session_id)

    uninstall = register_interrupt_callback(_interrupt)
    try:
        _ack(_SID_AC4_CN, status="idle", now=0.0)
        _ack(_SID_AC4_CN, status="running", now=700.0)
        stall_progress.update_runtime_facts(
            _SID_AC4_CN,
            status="running",
            elicitation_pending=False,
            pending_input_count=1,
            interrupt_clears_buffer=True,
        )
        decision = await evaluate_session(_SID_AC4_CN, now=700.0, settings=StallSettings())
    finally:
        uninstall()
    assert decision.mark_stalled is True
    assert decision.emit_stalled is True
    assert decision.interrupt is False
    assert decision.buffered_starts_next_turn is False
    assert calls == []


async def test_ac4_tick_resolves_harness_from_installed_conversation_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tick looks up the harness via ``get_conversation`` on the real store API."""
    for name in (
        "OMNIGENT_STALL_AFTER_S",
        "OMNIGENT_STALL_INTERRUPT_AFTER_S",
        "OMNIGENT_STALL_AUTO_INTERRUPT",
    ):
        monkeypatch.delenv(name, raising=False)
    claude_id = uuid.uuid4().hex
    codex_id = uuid.uuid4().hex
    conversations = {
        claude_id: SimpleNamespace(harness_override="claude-native"),
        codex_id: SimpleNamespace(harness_override="codex-native"),
    }
    store = MagicMock(spec=SqlAlchemyConversationStore)
    store.get_conversation.side_effect = conversations.get
    calls: list[str] = []

    async def _interrupt(session_id: str) -> None:
        calls.append(session_id)

    previous_store = runtime_globals._conversation_store
    runtime_globals._conversation_store = store
    uninstall_interrupt = register_interrupt_callback(_interrupt)
    uninstall_writer = install_label_writer(lambda sid, value: None)
    try:
        for session_id in (claude_id, codex_id):
            _ack(session_id, status="idle", now=0.0)
            _ack(session_id, status="running", now=700.0)
        await stall_watchdog.tick(now=700.0)
    finally:
        uninstall_writer()
        uninstall_interrupt()
        runtime_globals._conversation_store = previous_store
    claude_clock = stall_progress.get_clock(claude_id)
    assert claude_clock is not None and claude_clock.stalled is True
    assert calls.count(claude_id) == 0
    assert calls.count(codex_id) == 1


async def test_real_store_evaluates_a_uuid_and_skips_an_invalid_id(db_uri: str) -> None:
    """A real ConversationStore must not abort evaluate_session or tick.

    The full suite installs one. A 32-char hex id that is not a row is an
    unknown harness. A short id raises InvalidUuidError inside the store
    and is treated the same way, so the rest of the tick still runs.
    """
    from omnigent.util.uuid import InvalidUuidError

    store = SqlAlchemyConversationStore(db_uri)
    valid = "0123456789abcdef0123456789abcdef"
    invalid = "conv_ac3"
    assert store.get_conversation(valid) is None
    with pytest.raises(Exception) as raised:
        store.get_conversation(invalid)
    cause = raised.value.__cause__
    assert isinstance(raised.value, InvalidUuidError) or isinstance(cause, InvalidUuidError)

    previous_store = runtime_globals._conversation_store
    runtime_globals._conversation_store = store
    try:
        _ack(valid, status="idle", now=0.0)
        _ack(invalid, status="idle", now=0.0)
        valid_decision = await evaluate_session(valid, now=300.0, settings=StallSettings())
        invalid_decision = await evaluate_session(invalid, now=300.0, settings=StallSettings())
        await stall_watchdog.tick(now=301.0)
    finally:
        runtime_globals._conversation_store = previous_store
    assert valid_decision.mark_stalled is True
    assert invalid_decision.mark_stalled is True
    assert stall_progress.get_clock(valid) is not None
    assert stall_progress.get_clock(invalid) is not None


async def test_ac5_cooldown_blocks_a_second_interrupt() -> None:
    calls: list[str] = []

    async def _interrupt(session_id: str) -> None:
        calls.append(session_id)

    uninstall = register_interrupt_callback(_interrupt)
    events: list[dict] = []
    uninstall_publisher = install_publisher(lambda sid, event: events.append(event))
    try:
        _ack(_SID_AC5, status="idle", now=0.0)
        _ack(_SID_AC5, status="running", now=700.0)
        clock = stall_progress.get_clock(_SID_AC5)
        assert clock is not None
        clock.last_auto_interrupt_at = 640.0
        decision = await evaluate_session(_SID_AC5, now=700.0, settings=StallSettings())
    finally:
        uninstall()
        uninstall_publisher()
    assert decision.emit_stalled is True
    assert decision.interrupt is False
    assert calls == []
    assert [event["type"] for event in events] == ["session.stalled"]


def test_ac6_pending_elicitation_is_left_alone() -> None:
    _ack("conv_ac6", status="idle", now=0.0)
    clock = stall_progress.get_clock("conv_ac6")
    assert clock is not None
    clock.status = "running"
    clock.buffered_count = 2
    clock.elicitation_pending = True
    decision = evaluate(clock, 5000.0, StallSettings())
    assert decision == Decision(seconds_since_last_event=5000.0)
    committed = stall_progress.advance("conv_ac6", 5000.0, StallSettings())
    assert committed.mark_stalled is False
    assert committed.interrupt is False
    assert stall_progress.get_clock("conv_ac6").stalled is False  # type: ignore[union-attr]


async def test_ac7_retry_with_same_key_stores_one_item() -> None:
    stored: list[str] = []

    async def _produce() -> dict[str, str]:
        stored.append("item_1")
        return {"queued": "true", "item_id": "item_1"}

    first = await resolve_idempotent("conv_ac7", "same-key", _produce)
    second = await resolve_idempotent("conv_ac7", "same-key", _produce)
    assert first == second
    assert first["item_id"] == "item_1"
    assert stored == ["item_1"]

    seen: list[str] = []

    async def _operation(key: str) -> dict[str, bool]:
        seen.append(key)
        if len(seen) == 1:
            raise httpx.ReadTimeout("timed out")
        return {"ok": True}

    result = await retry_same_key(
        _operation,
        key="same-key",
        retry_exceptions=(httpx.TimeoutException,),
        sleep=lambda _delay: asyncio.sleep(0),
    )
    assert result == {"ok": True}
    assert seen == ["same-key", "same-key"]


def test_ac8_compaction_progress_is_not_stalled() -> None:
    for moment in range(0, 501, 100):
        stall_progress.observe_published_event(
            "conv_ac8",
            {"type": "response.compaction.in_progress"},
            now=float(moment),
        )
    decision = stall_progress.advance("conv_ac8", 500.0, StallSettings())
    clock = stall_progress.get_clock("conv_ac8")
    assert clock is not None
    assert decision.mark_stalled is False
    assert clock.stalled is False


def test_heartbeat_does_not_reset_the_stall_clock() -> None:
    _ack("conv_heartbeat", status="idle", now=0.0)
    stall_progress.observe_published_event(
        "conv_heartbeat", {"type": "session.heartbeat"}, now=500.0
    )
    decision = stall_progress.advance("conv_heartbeat", 500.0, StallSettings())
    clock = stall_progress.get_clock("conv_heartbeat")
    assert clock is not None
    assert decision.mark_stalled is True
    assert clock.stalled is True
    assert clock.last_event_at == 0.0


def test_progress_without_status_entry_does_not_stall() -> None:
    stall_progress.observe_published_event(
        "conv_no_status", {"type": "session.changed_files.invalidated"}, now=1000.0
    )
    stall_progress.update_runtime_facts(
        "conv_no_status", status=None, elicitation_pending=False, pending_input_count=0
    )
    decision = stall_progress.advance("conv_no_status", 1301.0, StallSettings())
    clock = stall_progress.get_clock("conv_no_status")
    assert clock is not None
    assert decision.mark_stalled is False
    assert decision.emit_stalled is False
    assert clock.stalled is False
    assert clock.status == "idle"


def test_ac9_idle_watchdog_default_and_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from omnigent.runtime.harnesses import _scaffold

    assert _scaffold._DEFAULT_TURN_IDLE_TIMEOUT_S == 1200.0
    monkeypatch.setattr(_scaffold, "_TURN_IDLE_TIMEOUT_S", _scaffold._DEFAULT_TURN_IDLE_TIMEOUT_S)
    monkeypatch.delenv("HARNESS_TURN_TIMEOUT_S", raising=False)
    assert _scaffold.resolve_turn_idle_timeout_s() == 1200.0
    monkeypatch.setenv("HARNESS_TURN_TIMEOUT_S", "42")
    assert _scaffold.resolve_turn_idle_timeout_s() == 42.0
    monkeypatch.setenv("HARNESS_TURN_TIMEOUT_S", "0")
    assert _scaffold.resolve_turn_idle_timeout_s() == 0.0


def test_ac10_progress_emits_recovered_once() -> None:
    _ack("conv_ac10", status="idle", now=0.0)
    stall_progress.advance("conv_ac10", 400.0, StallSettings(auto_interrupt=False))
    assert stall_progress.get_clock("conv_ac10").stalled is True  # type: ignore[union-attr]
    stall_progress.observe_published_event(
        "conv_ac10", {"type": "response.output_text.delta", "delta": "x"}, now=401.0
    )
    recovered = stall_progress.drain_pending_publishes()
    assert recovered == [
        ("conv_ac10", {"type": "session.recovered", "conversation_id": "conv_ac10"})
    ]
    assert stall_progress.get_clock("conv_ac10").stalled is False  # type: ignore[union-attr]
    stall_progress.observe_published_event(
        "conv_ac10", {"type": "response.output_text.delta", "delta": "y"}, now=402.0
    )
    assert stall_progress.drain_pending_publishes() == []


def test_publish_hook_recovers_on_progress_and_ignores_heartbeat() -> None:
    _ack("conv_hook", status="idle", now=0.0)
    stall_progress.advance("conv_hook", 400.0, StallSettings(auto_interrupt=False))
    session_stream.publish("conv_hook", {"type": "response.compaction.in_progress"})
    clock = stall_progress.get_clock("conv_hook")
    assert clock is not None
    assert clock.stalled is False
    assert clock.pending_recovered is False
    progressed_at = clock.last_event_at
    assert progressed_at is not None and progressed_at > 0
    stall_progress.advance("conv_hook", progressed_at + 400.0, StallSettings(auto_interrupt=False))
    assert stall_progress.get_clock("conv_hook").stalled is True  # type: ignore[union-attr]
    session_stream.publish("conv_hook", {"type": "session.heartbeat"})
    after = stall_progress.get_clock("conv_hook")
    assert after is not None
    assert after.stalled is True
    assert after.last_event_at == progressed_at


def test_ac11_auto_interrupt_off_still_marks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNIGENT_STALL_AFTER_S", raising=False)
    monkeypatch.delenv("OMNIGENT_STALL_INTERRUPT_AFTER_S", raising=False)
    monkeypatch.delenv("OMNIGENT_STALL_AUTO_INTERRUPT", raising=False)
    defaults = load_settings()
    assert defaults.stall_after_s == 300.0
    assert defaults.interrupt_after_s == 600.0
    assert defaults.auto_interrupt is True
    assert defaults.cooldown_s == 1800.0

    monkeypatch.setenv("OMNIGENT_STALL_AUTO_INTERRUPT", "0")
    disabled = load_settings()
    assert disabled.auto_interrupt is False
    _ack("conv_ac11", status="idle", now=0.0)
    _ack("conv_ac11", status="running", now=700.0)
    decision = evaluate(
        stall_progress.get_clock("conv_ac11"),  # type: ignore[arg-type]
        700.0,
        disabled,
    )
    assert decision.mark_stalled is True
    assert decision.emit_stalled is True
    assert decision.interrupt is False


def test_ac12_stall_behaviour_ignores_team_chat_flag() -> None:
    for relative in (
        "omnigent/runtime/stall_progress.py",
        "omnigent/server/stall_watchdog.py",
    ):
        assert "team_chat" not in (_ROOT / relative).read_text(encoding="utf-8")
    clock = Clock(
        status="running",
        turn_started_at=0.0,
        last_event_at=0.0,
        buffered_count=1,
    )
    first = evaluate(clock, 700.0, StallSettings())
    second = evaluate(clock, 700.0, StallSettings())
    assert first == second
    assert first.interrupt is True


def _enable_webhook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "manager_webhook:\n"
        "  enabled: true\n"
        "  endpoint: https://bot.example.com/hook\n"
        "  public_base_url: https://omnigent.example.com\n"
        "  summary_max_chars: 40\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OMNIGENT_CONFIG", str(path))
    monkeypatch.setenv("OMNIGENT_MANAGER_WEBHOOK_SECRET", "test-secret")
    reset_manager_webhook_config_cache()


async def test_ac13_webhook_receives_stalled_and_recovered(
    db_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_webhook(tmp_path, monkeypatch)
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_outbox.configure(store)
    session_id = uuid.uuid4().hex
    try:
        _ack(session_id, status="idle", now=0.0)
        decision = stall_progress.advance(session_id, 400.0, StallSettings(auto_interrupt=False))
        await apply_effects(session_id, decision)
        await apply_effects(session_id, decision)
        stall_progress.observe_published_event(
            session_id, {"type": "response.output_text.delta", "delta": "x"}, now=401.0
        )
        recovered = stall_progress.drain_pending_publishes()
        assert len(recovered) == 1
        assert recovered[0][1]["type"] == "session.recovered"
        rows, _cursor = store.list_deliveries(session_id)
    finally:
        session_outbox.reset_for_tests()
        reset_manager_webhook_config_cache()
    kinds = [row.event_type for row in rows]
    assert kinds.count("session.stalled") == 1
    assert kinds.count("session.recovered") == 1
    stalled = next(row for row in rows if row.event_type == "session.stalled")
    assert stalled.payload
    assert "secret" not in stalled.payload


def test_stall_interrupt_reuses_the_event_path_without_a_wake_key() -> None:
    source = (_ROOT / "omnigent/server/routes/sessions/routes_events.py").read_text(
        encoding="utf-8"
    )
    start = source.index("async def _deliver_stall_interrupt")
    end = source.index("register_interrupt_callback", start)
    body = source[start:end]
    assert 'SessionEventInput(type="interrupt")' in body
    assert "subagent_wake_key" not in body
    assert "_post_event_impl" in body


class _Request:
    def __init__(self) -> None:
        self.app = object()
        self.headers: dict[str, str] = {}
        self.scope: dict[str, str] = {}


class _FakeClient:
    calls: list[dict] = []
    outcomes: list[object] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        del exc
        return False

    async def request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        del content, timeout
        _FakeClient.calls.append({"method": method, "path": path, "headers": dict(headers or {})})
        outcome = _FakeClient.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, httpx.Response)
        return outcome


def _response(status: int, body: str) -> httpx.Response:
    return httpx.Response(status, text=body)


async def test_mcp_event_post_retries_timeout_with_one_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeClient.calls = []
    _FakeClient.outcomes = [
        httpx.ReadTimeout("timed out"),
        _response(202, '{"queued": true, "item_id": "item_1"}'),
    ]
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    # The endpoint module binds the name httpx, so patch that reference.
    monkeypatch.setattr("omnigent.server.mcp_endpoint.httpx.AsyncClient", _FakeClient)
    api = _Api(_Request(), None)  # type: ignore[arg-type]
    body = await api.call("POST", "/v1/sessions/conv_ac7/events", {"type": "message"})
    assert body["item_id"] == "item_1"
    assert len(_FakeClient.calls) == 2
    keys = [call["headers"]["Idempotency-Key"] for call in _FakeClient.calls]
    assert keys[0] == keys[1]
    assert keys[0]


async def test_mcp_non_event_timeout_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeClient.calls = []
    _FakeClient.outcomes = [httpx.ReadTimeout("timed out")]
    monkeypatch.setattr("omnigent.server.mcp_endpoint.httpx.AsyncClient", _FakeClient)
    api = _Api(_Request(), None)  # type: ignore[arg-type]
    with pytest.raises(ToolFailure, match="timed out"):
        await api.call("GET", "/v1/sessions/conv_ac7")
    assert len(_FakeClient.calls) == 1


async def test_mcp_event_post_does_not_retry_client_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeClient.calls = []
    _FakeClient.outcomes = [_response(404, '{"error": {"message": "missing"}}')]
    monkeypatch.setattr("omnigent.server.mcp_endpoint.httpx.AsyncClient", _FakeClient)
    api = _Api(_Request(), None)  # type: ignore[arg-type]
    with pytest.raises(ToolFailure) as caught:
        await api.call("POST", "/v1/sessions/conv_ac7/events", {"type": "message"})
    assert caught.value.http_status == 404
    assert len(_FakeClient.calls) == 1


def test_waiting_parent_is_not_an_active_turn() -> None:
    ack = _ack("conv_waiting", status="waiting", now=50.0)
    assert ack["delivery"] == "turn_started"
    assert "active_turn_age_s" not in ack

    _ack("conv_waiting_tick", status="idle", now=0.0)
    _ack("conv_waiting_tick", status="running", now=10.0)
    stall_progress.update_runtime_facts(
        "conv_waiting_tick",
        status="waiting",
        elicitation_pending=False,
        pending_input_count=0,
    )
    clock = stall_progress.get_clock("conv_waiting_tick")
    assert clock is not None
    assert clock.status == "waiting"
    assert clock.buffered_count == 0
    decision = evaluate(clock, 2000.0, StallSettings())
    assert decision.mark_stalled is False
    assert decision.interrupt is False


def test_seed_skips_waiting_parents() -> None:
    from omnigent.server.routes._sessions.helpers import _session_status_cache

    _session_status_cache["conv_seed_waiting"] = "waiting"
    _session_status_cache["conv_seed_running"] = "running"
    try:
        stall_watchdog._seed_running(0.0)
        assert stall_progress.get_clock("conv_seed_waiting") is None
        seeded = stall_progress.get_clock("conv_seed_running")
        assert seeded is not None and seeded.status == "running"
    finally:
        _session_status_cache.pop("conv_seed_waiting", None)
        _session_status_cache.pop("conv_seed_running", None)


async def test_next_turn_clears_a_buffer_so_silence_does_not_interrupt() -> None:
    calls: list[str] = []

    async def _interrupt(session_id: str) -> None:
        calls.append(session_id)

    uninstall = register_interrupt_callback(_interrupt)
    try:
        _ack(_SID_DRAIN, status="idle", now=0.0)
        stall_progress.observe_published_event(
            _SID_DRAIN,
            {"type": "response.in_progress", "response": {"id": "resp_1"}},
            now=1.0,
        )
        _ack(_SID_DRAIN, status="running", now=10.0)
        clock = stall_progress.get_clock(_SID_DRAIN)
        assert clock is not None and clock.buffered_count == 1
        stall_progress.observe_published_event(
            _SID_DRAIN,
            {"type": "response.in_progress", "response": {"id": "resp_1"}},
            now=15.0,
        )
        assert stall_progress.get_clock(_SID_DRAIN).buffered_count == 1  # type: ignore[union-attr]
        stall_progress.observe_published_event(
            _SID_DRAIN,
            {"type": "response.created", "response": {"id": "resp_2"}},
            now=20.0,
        )
        clock = stall_progress.get_clock(_SID_DRAIN)
        assert clock is not None
        assert clock.buffered_count == 0
        assert clock.turn_started_at == 20.0
        ack = _ack(_SID_DRAIN, status="running", now=30.0)
        assert ack["active_turn_age_s"] == 10.0
        # That ack buffers again. Drop it the way the next turn would,
        # then sit in silence past the interrupt threshold.
        stall_progress.observe_published_event(
            _SID_DRAIN,
            {"type": "response.in_progress", "response": {"id": "resp_3"}},
            now=31.0,
        )
        decision = await evaluate_session(_SID_DRAIN, now=700.0, settings=StallSettings())
    finally:
        uninstall()
    assert decision.interrupt is False
    assert decision.mark_stalled is True
    assert calls == []


def test_native_consume_clears_only_a_drained_pending() -> None:
    _ack("conv_native", status="idle", now=0.0)
    _ack("conv_native", status="running", now=5.0)
    stall_progress.observe_published_event(
        "conv_native",
        {"type": "session.input.consumed", "data": {"item_id": "item_1"}},
        now=6.0,
    )
    clock = stall_progress.get_clock("conv_native")
    assert clock is not None and clock.buffered_count == 1
    stall_progress.observe_published_event(
        "conv_native",
        {
            "type": "session.input.consumed",
            "data": {"item_id": "item_1", "cleared_pending_id": "pending_1"},
        },
        now=7.0,
    )
    assert stall_progress.get_clock("conv_native").buffered_count == 0  # type: ignore[union-attr]


async def test_stall_outbox_key_differs_after_restart(
    db_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_webhook(tmp_path, monkeypatch)
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_outbox.configure(store)
    session_id = uuid.uuid4().hex
    try:
        _ack(session_id, status="idle", now=0.0)
        first = stall_progress.advance(session_id, 400.0, StallSettings(auto_interrupt=False))
        await apply_effects(session_id, first)
        stall_progress.reset_for_tests()
        _ack(session_id, status="idle", now=1000.0)
        second = stall_progress.advance(session_id, 1400.0, StallSettings(auto_interrupt=False))
        await apply_effects(session_id, second)
        rows, _cursor = store.list_deliveries(session_id)
    finally:
        session_outbox.reset_for_tests()
        reset_manager_webhook_config_cache()
    stalled = [row for row in rows if row.event_type == "session.stalled"]
    assert len(stalled) == 2
    assert stalled[0].transition_key != stalled[1].transition_key
    assert {row.transition_key for row in stalled} == {
        "stall:0:stalled",
        "stall:1000000:stalled",
    }


async def test_same_key_with_a_different_body_does_not_store_or_return_the_ack() -> None:
    stored: list[str] = []

    async def _produce() -> dict[str, str]:
        stored.append("item")
        return {"item_id": "item_1"}

    first = await resolve_idempotent("conv_body", "same-key", _produce, fingerprint="aaa")
    assert first["item_id"] == "item_1"
    with pytest.raises(IdempotencyConflict):
        await resolve_idempotent("conv_body", "same-key", _produce, fingerprint="bbb")
    assert stored == ["item"]


async def test_idempotent_event_post_hides_the_ack_from_a_stranger(
    auth_client: httpx.AsyncClient,
) -> None:
    from tests.server.helpers import create_test_agent

    owner = "owner@example.com"
    agent = await create_test_agent(auth_client, name="stall-idem-auth", user=owner)
    session_id = agent["_session_id"]
    body = {
        "type": "external_conversation_item",
        "data": {
            "item_type": "message",
            "item_data": {
                "role": "user",
                "content": [{"type": "input_text", "text": "hello from the owner"}],
            },
        },
    }
    owner_headers = {"X-Forwarded-Email": owner, "Idempotency-Key": "stall-key-1"}
    first = await auth_client.post(
        f"/v1/sessions/{session_id}/events", json=body, headers=owner_headers
    )
    assert first.status_code == 202, first.text
    item_id = first.json()["item_id"]
    retry = await auth_client.post(
        f"/v1/sessions/{session_id}/events", json=body, headers=owner_headers
    )
    assert retry.status_code == 202, retry.text
    assert retry.json()["item_id"] == item_id
    stranger = await auth_client.post(
        f"/v1/sessions/{session_id}/events",
        json=body,
        headers={"X-Forwarded-Email": "stranger@example.com", "Idempotency-Key": "stall-key-1"},
    )
    assert stranger.status_code == 404
    assert item_id not in stranger.text
    other = {
        "type": "external_conversation_item",
        "data": {
            "item_type": "message",
            "item_data": {
                "role": "user",
                "content": [{"type": "input_text", "text": "a different body"}],
            },
        },
    }
    mismatch = await auth_client.post(
        f"/v1/sessions/{session_id}/events", json=other, headers=owner_headers
    )
    assert mismatch.status_code == 409, mismatch.text
    listed = await auth_client.get(
        f"/v1/sessions/{session_id}/items",
        headers={"X-Forwarded-Email": owner},
    )
    assert listed.status_code == 200, listed.text
    ids = [item["id"] for item in listed.json()["data"]]
    assert ids.count(item_id) == 1


def test_each_event_loop_owns_its_stall_watchdog() -> None:
    """A nested app must not await another loop's stall tick.

    Each TestClient lifespan runs on its own portal loop. Reusing one
    process-global task made the inner client's shutdown await the outer
    tick and raise ``attached to a different loop``.
    """
    loop_a = asyncio.new_event_loop()
    loop_b = asyncio.new_event_loop()

    async def _start() -> asyncio.Task[None]:
        return stall_watchdog.start_stall_watchdog()

    try:
        task_a = loop_a.run_until_complete(_start())
        task_b = loop_b.run_until_complete(_start())
        assert task_a is not task_b
        assert task_a.get_loop() is loop_a
        assert task_b.get_loop() is loop_b
        assert loop_a.run_until_complete(_start()) is task_a

        loop_b.run_until_complete(stall_watchdog.stop_stall_watchdog())
        assert task_b.done()
        assert not task_a.done()

        loop_a.run_until_complete(stall_watchdog.stop_stall_watchdog())
        assert task_a.done()
    finally:
        for loop in (loop_a, loop_b):
            if loop.is_closed():
                continue
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()


def test_client_retry_import_does_not_load_the_database() -> None:
    """Host startup imports the Python client, which must not load omnigent.db."""
    code = """
import importlib.abc
import sys

class NoDatabaseImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "omnigent.db" or fullname.startswith("omnigent.db."):
            raise ImportError("client retry must not import " + fullname)

sys.meta_path.insert(0, NoDatabaseImports())
from omnigent_client._sessions import retry_same_key
assert retry_same_key.__name__ == "retry_same_key"
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
