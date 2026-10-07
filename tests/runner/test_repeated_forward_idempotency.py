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
from omnigent.spec.types import AgentSpec
from tests.runner.conftest import (
    _BlockingHarnessClient,
    _build_app_for_spec,
    _FakeProcessManager,
    _runner_client,
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
