"""Busy-parent buffering for a sub-agent wake notice."""

from __future__ import annotations

import asyncio
import contextlib

import httpx

from omnigent.runner import create_runner_app
from omnigent.runner.subagent_work import _deliver_subagent_wake_post, subagent_wake_message_id
from tests.runner.helpers import NullServerClient


class _RecordingServer:
    """Captures one wake POST body."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    async def post(self, url: str, *, json: dict[str, object], timeout: object) -> httpx.Response:
        del timeout
        self.payloads.append({"url": url, **json})
        request = httpx.Request("POST", f"http://test{url}")
        return httpx.Response(202, json={"queued": True}, request=request)


async def test_wake_post_carries_shared_dedupe_key() -> None:
    """The runner wake names the same id the server uses for that child turn."""
    client = _RecordingServer()
    child_id = "conv_child"
    turn_key = "resp_turn"
    wake_key = subagent_wake_message_id(child_id, turn_key)
    notice = (
        "[System: sub-agent worker/auth finished (completed) — "
        "1 result waiting in inbox. Call sys_read_inbox to collect.]"
    )
    delivered = await _deliver_subagent_wake_post(
        client,  # type: ignore[arg-type]
        "conv_parent",
        notice,
        wake_key=wake_key,
    )
    assert delivered is True
    assert client.payloads[0]["subagent_wake_key"] == wake_key
    data = client.payloads[0]["data"]
    assert isinstance(data, dict)
    assert data["stable_id"] == wake_key


class _IdleProcessManager:
    """Present so the events route accepts a message; the buffered wake never calls it."""

    handles_tool_dispatch = True


async def test_busy_parent_buffers_wake_then_starts_turn() -> None:
    """A wake that arrives mid-turn is queued and dispatched once the turn ends."""
    conv = "conv_busy_parent"
    notice = (
        "[System: sub-agent worker/auth finished (completed) — "
        "1 result waiting in inbox. Call sys_read_inbox to collect.]"
    )
    app = create_runner_app(
        process_manager=_IdleProcessManager(),  # type: ignore[arg-type]
        server_client=NullServerClient(),  # type: ignore[arg-type]
    )
    app.state.active_turns[conv] = None
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://runner") as client:
        resp = await client.post(
            f"/v1/sessions/{conv}/events",
            json={"type": "message", "role": "user", "content": notice},
        )
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "buffered"
    buffered = app.state.session_message_buffers.get(conv)
    assert buffered
    assert notice in str(buffered[-1].get("content"))

    app.state.active_turns.pop(conv)
    await app.state.check_and_start_next_turn(conv)
    assert not app.state.session_message_buffers.get(conv)
    statuses: list[str] = []
    queue = app.state.session_event_queues.get(conv)
    while queue is not None and not queue.empty():
        event = queue.get_nowait()
        if isinstance(event, dict) and event.get("type") == "session.status":
            statuses.append(str(event.get("status")))
    assert "running" in statuses
    task = app.state.active_turns.get(conv)
    if isinstance(task, asyncio.Task) and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
