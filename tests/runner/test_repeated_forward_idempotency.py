"""A message forward the server repeats after a tunnel drop runs the prompt once.

The server persists a user message, forwards it with its ``persisted_item_id``,
and repeats the forward when the runner tunnel drops before the response
arrives. The first frame may already have reached the runner, so the runner
acknowledges a repeat of a message it has taken instead of running it again.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest
from fastapi import FastAPI

from omnigent.runner import create_runner_app
from omnigent.runner.session_history import trailing_user_item_id
from omnigent.spec.types import AgentSpec
from tests.runner.conftest import (
    _BlockingHarnessClient,
    _build_app_for_spec,
    _FakeProcessManager,
    _runner_client,
    _ScriptedHarnessClient,
    _sse,
)
from tests.runner.helpers import NullServerClient
from tests.runner.native_helpers import _harness_spec

AGENT_ID = "ag_repeat_forward"
SESSION_ID = "conv_repeat_forward"
EVENTS_PATH = f"/v1/sessions/{SESSION_ID}/events"

NATIVE_AGENT_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
NATIVE_SESSION_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
NATIVE_EVENTS_PATH = f"/v1/sessions/{NATIVE_SESSION_ID}/events"


def _build_app(gate: asyncio.Event) -> tuple[FastAPI, _BlockingHarnessClient]:
    """Runner app whose harness holds the first turn open until *gate* is set."""
    spec = AgentSpec(spec_version=1, name="t")
    frames = [
        _sse({"type": "response.created", "response": {"id": "resp_1"}}),
        _sse({"type": "response.output_text.delta", "delta": "hi"}),
        _sse({"type": "response.completed", "response": {"id": "resp_1"}}),
    ]
    harness = _BlockingHarnessClient(frames, gate)

    async def _resolver(agent_id: str, session_id: str | None = None) -> AgentSpec:
        del agent_id, session_id
        return spec

    app = create_runner_app(
        process_manager=_FakeProcessManager(harness),  # type: ignore[arg-type]
        spec_resolver=_resolver,
        server_client=NullServerClient(),  # type: ignore[arg-type]
    )
    return app, harness


def _forward(item_id: str, text: str) -> dict[str, Any]:
    """The body the server forwards for a persisted user message."""
    return {
        "type": "message",
        "role": "user",
        "agent_id": AGENT_ID,
        "content": [{"type": "input_text", "text": text}],
        "persisted_item_id": item_id,
    }


@pytest.mark.asyncio
async def test_repeated_forward_of_a_taken_message_is_acknowledged_not_run_again() -> None:
    """A repeat of a running or buffered message is acknowledged; other items still queue."""
    from omnigent.runner.app import _session_histories_ref

    gate = asyncio.Event()
    app, harness = _build_app(gate)
    buffers = app.state.session_message_buffers
    try:
        async with _runner_client(app) as client:
            first = await client.post(EVENTS_PATH, json=_forward("msg_001", "hello"))
            assert first.status_code == 202, first.text
            assert first.json()["status"] == "accepted"
            await asyncio.wait_for(harness.post_seen.wait(), timeout=5.0)

            # The server's repeat of the running message: nothing queued, no second turn.
            repeat = await client.post(EVENTS_PATH, json=_forward("msg_001", "hello"))
            assert repeat.status_code == 202, repeat.text
            assert repeat.json()["status"] == "accepted"
            assert buffers.get(SESSION_ID, []) == []
            assert len(harness.posted_bodies) == 1

            # A different persisted item is new input and queues behind the turn once,
            # however often its forward is repeated.
            other = await client.post(EVENTS_PATH, json=_forward("msg_002", "and this"))
            assert other.status_code == 202, other.text
            assert other.json()["status"] == "buffered"
            repeat_other = await client.post(EVENTS_PATH, json=_forward("msg_002", "and this"))
            assert repeat_other.json()["status"] == "accepted"
            assert [m["persisted_item_id"] for m in buffers[SESSION_ID]] == ["msg_002"]

            gate.set()
            deadline = asyncio.get_running_loop().time() + 10.0
            while len(harness.posted_bodies) < 2 and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.02)
            # The first turn and the queued item each ran exactly once, and the
            # second run is the queued item itself — not a re-run of the first.
            assert len(harness.posted_bodies) == 2
            assert harness.turn_user_texts[0][-1] == "hello"
            assert harness.turn_user_texts[1][-1] == "and this"
    finally:
        gate.set()
        _session_histories_ref.pop(SESSION_ID, None)


@pytest.mark.asyncio
async def test_deduplicated_native_forward_does_not_remark_the_turn_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deduplicated repeat on a native session must not re-mark the turn running.

    ``note_session_turn_started`` sets the status memo to ``running`` and leans
    on the turn's completion to flip it back to ``idle``. A repeat whose original
    turn is already gone starts no turn, so the note must fire only once the
    runner commits to a turn, never from the dedup acknowledgement.
    """
    from omnigent.runner.app import _session_histories_ref

    app, _ = await _build_app_for_spec(_harness_spec("claude-native"))
    registry = app.state.session_resource_registry
    note_calls: list[str] = []
    _real_note = registry.note_session_turn_started

    def _spy_note(session_id: str) -> None:
        note_calls.append(session_id)
        _real_note(session_id)

    monkeypatch.setattr(registry, "note_session_turn_started", _spy_note)

    try:
        async with _runner_client(app) as client:
            create = await client.post(
                "/v1/sessions",
                json={"session_id": NATIVE_SESSION_ID, "agent_id": NATIVE_AGENT_ID},
            )
            assert create.status_code == 201, create.text
            note_calls.clear()

            # Pin an active turn so the forward buffers without launching a turn.
            holder = asyncio.ensure_future(asyncio.Event().wait())
            app.state.active_turns[NATIVE_SESSION_ID] = holder
            try:
                first = await client.post(NATIVE_EVENTS_PATH, json=_forward("msg_dedup", "hi"))
                assert first.status_code == 202, first.text
                assert first.json()["status"] == "buffered"
                assert note_calls == [NATIVE_SESSION_ID]

                repeat = await client.post(NATIVE_EVENTS_PATH, json=_forward("msg_dedup", "hi"))
                assert repeat.status_code == 202, repeat.text
                assert repeat.json()["status"] == "accepted"
                # The deduped repeat starts no turn, so it must not re-mark running.
                assert note_calls == [NATIVE_SESSION_ID]
            finally:
                app.state.active_turns.pop(NATIVE_SESSION_ID, None)
                holder.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await holder
    finally:
        _session_histories_ref.pop(NATIVE_SESSION_ID, None)


class _FailFirstSpawnProcessManager(_FakeProcessManager):
    """Fails the first harness spawn, then behaves like the base manager.

    Models a transient ``get_client`` spawn failure: the first turn setup
    returns a harness-spawn error before any turn runs, while a later turn gets
    a working client.
    """

    def __init__(self, client: _ScriptedHarnessClient) -> None:
        super().__init__(client)
        self._fail_next = True

    async def get_client(
        self, conversation_id: str, harness: str, env: Any = None
    ) -> _ScriptedHarnessClient:
        if self._fail_next:
            self._fail_next = False
            self.get_client_calls.append((conversation_id, harness, env))
            raise RuntimeError("transient harness spawn failure")
        return await super().get_client(conversation_id, harness, env)


def _build_app_with_manager(manager: _FakeProcessManager) -> FastAPI:
    """Runner app over a caller-supplied process manager."""
    spec = AgentSpec(spec_version=1, name="t")

    async def _resolver(agent_id: str, session_id: str | None = None) -> AgentSpec:
        del agent_id, session_id
        return spec

    return create_runner_app(
        process_manager=manager,  # type: ignore[arg-type]
        spec_resolver=_resolver,
        server_client=NullServerClient(),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_setup_failure_forgets_the_accept_so_a_repeated_forward_retries() -> None:
    """A forward whose turn setup fails is retried by the server's repeat.

    The runner accepts the forward and launches the background turn, but the
    harness spawn fails before any turn runs. The accept marker must be dropped
    so the server's repeat of the same ``persisted_item_id`` runs the message
    instead of being acknowledged as already taken and silently lost.
    """
    from omnigent.runner.app import _session_histories_ref

    frames = [
        _sse({"type": "response.created", "response": {"id": "resp_1"}}),
        _sse({"type": "response.completed", "response": {"id": "resp_1"}}),
    ]
    harness = _ScriptedHarnessClient(frames)
    manager = _FailFirstSpawnProcessManager(harness)
    app = _build_app_with_manager(manager)
    try:
        async with _runner_client(app) as client:
            first = await client.post(EVENTS_PATH, json=_forward("msg_fail", "hello"))
            assert first.status_code == 202, first.text

            # The failed setup turn drops its slot right after forgetting the
            # accept marker, so a cleared slot means the forget has run.
            deadline = asyncio.get_running_loop().time() + 10.0
            while (
                not manager.get_client_calls or SESSION_ID in app.state.active_turns
            ) and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.02)
            assert manager.get_client_calls, "first turn never attempted a spawn"
            assert SESSION_ID not in app.state.active_turns
            assert harness.posted_bodies == []

            # The server repeats the forward; the marker is gone, so it runs.
            repeat = await client.post(EVENTS_PATH, json=_forward("msg_fail", "hello"))
            assert repeat.status_code == 202, repeat.text
            assert repeat.json()["detail"] == "Turn started."
            deadline = asyncio.get_running_loop().time() + 10.0
            while not harness.posted_bodies and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.02)
            assert len(harness.posted_bodies) == 1
    finally:
        _session_histories_ref.pop(SESSION_ID, None)


def test_trailing_user_item_id_prefers_the_user_message_over_a_later_item() -> None:
    """The dedup id is the last user message, not a later item conversion drops."""
    assert (
        trailing_user_item_id(
            [
                {"id": "a", "type": "message", "role": "assistant", "content": []},
                {"id": "b", "type": "message", "role": "user", "content": []},
            ]
        )
        == "b"
    )
    # A trailing non-message item (dropped by input conversion) must not win.
    assert (
        trailing_user_item_id(
            [
                {"id": "b", "type": "message", "role": "user", "content": []},
                {"id": "c", "type": "reasoning", "summary": "…"},
            ]
        )
        == "b"
    )
    assert trailing_user_item_id([]) is None
    assert trailing_user_item_id([{"type": "message", "role": "assistant", "content": []}]) is None
    assert trailing_user_item_id([{"type": "message", "role": "user"}]) is None


def test_create_runner_app_mints_a_fresh_dedup_epoch_per_process() -> None:
    """Each runner process gets its own forward-dedup epoch.

    The epoch scopes the in-memory accept ledger to this process, so the server
    repeats a forward only while the advertising process is still on the tunnel.
    A separate app models a same-id restart: its empty ledger must carry a
    different epoch so the server declines the repeat instead of re-running it.
    """
    first = _build_app_with_manager(_FakeProcessManager(_ScriptedHarnessClient([])))
    second = _build_app_with_manager(_FakeProcessManager(_ScriptedHarnessClient([])))
    assert isinstance(first.state.runner_dedup_epoch, str)
    assert first.state.runner_dedup_epoch
    assert first.state.runner_dedup_epoch != second.state.runner_dedup_epoch
