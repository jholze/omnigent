"""A message forward the server repeats after a tunnel drop runs the prompt once.

The server persists a user message, forwards it with its ``persisted_item_id``,
and repeats the forward when the runner tunnel drops before the response
arrives. The first frame may already have reached the runner, so the runner
acknowledges a repeat of a message it has taken instead of running it again.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI

from omnigent.runner import create_runner_app
from omnigent.spec.types import AgentSpec
from tests.runner.conftest import (
    _BlockingHarnessClient,
    _FakeProcessManager,
    _runner_client,
    _sse,
)
from tests.runner.helpers import NullServerClient

AGENT_ID = "ag_repeat_forward"
SESSION_ID = "conv_repeat_forward"
EVENTS_PATH = f"/v1/sessions/{SESSION_ID}/events"


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
            # The first turn and the queued item each ran exactly once.
            assert len(harness.posted_bodies) == 2
    finally:
        gate.set()
        _session_histories_ref.pop(SESSION_ID, None)
