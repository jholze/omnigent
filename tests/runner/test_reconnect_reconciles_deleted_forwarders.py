"""Regression test: reconnect must reap forwarders for sessions deleted offline.

A native session's transcript forwarder is a restart-forever task keyed by
session id in ``orchestration._AUTO_FORWARDER_TASKS``. When a session is deleted
while this runner's tunnel is down, the server takes its offline path and skips
runner-side cleanup, and nothing replays that cleanup when the tunnel comes
back. The session's forwarder therefore survives and keeps tailing its pane and
POSTing events to a session that no longer exists.

On reconnect the runner now asks the server about each live forwarder's session.
A definitive 404 means the session was deleted during the gap, so the forwarder
is cancelled. A transient error or a still-live (200) session is left untouched,
because an ordinary recoverable error must not tear a healthy forwarder down.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

import httpx
from fastapi import FastAPI

from omnigent.runner import create_runner_app
from omnigent.runner.native import orchestration as orch


async def _forever() -> None:
    await asyncio.sleep(3600)


def _register_forwarder(session_id: str) -> asyncio.Task[object]:
    task: asyncio.Task[object] = asyncio.ensure_future(_forever())
    task.set_name(f"claude-forwarder-{session_id}")
    orch._register_auto_forwarder_task(session_id, task)
    return task


def _make_app(handler) -> tuple[FastAPI, httpx.AsyncClient]:
    server_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://server"
    )
    app = create_runner_app(server_client=server_client)
    return app, server_client


async def _drain(session_id: str, task: asyncio.Task[object]) -> None:
    orch._AUTO_FORWARDER_TASKS.pop(session_id, None)
    if not task.done():
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_reconnect_cancels_forwarder_for_session_deleted_offline() -> None:
    deleted = f"conv_deleted_{uuid.uuid4().hex}"
    alive = f"conv_alive_{uuid.uuid4().hex}"

    def handler(request: httpx.Request) -> httpx.Response:
        session_id = request.url.path.rsplit("/", 1)[-1]
        if session_id == deleted:
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json={"id": session_id, "agent_id": "agent_x"})

    app, server_client = _make_app(handler)
    deleted_task = _register_forwarder(deleted)
    alive_task = _register_forwarder(alive)
    try:
        await app.state.reconcile_forwarders_after_reconnect()

        # The session the server reports gone (404) was deleted during the
        # disconnect, so its leaked forwarder is cancelled and dropped.
        assert deleted_task.cancelled()
        assert deleted not in orch._AUTO_FORWARDER_TASKS

        # The still-live session's forwarder must survive untouched.
        assert not alive_task.cancelled()
        assert alive in orch._AUTO_FORWARDER_TASKS
    finally:
        await server_client.aclose()
        await _drain(deleted, deleted_task)
        await _drain(alive, alive_task)


async def test_reconnect_keeps_forwarder_on_transient_error() -> None:
    session_id = f"conv_transient_{uuid.uuid4().hex}"

    def handler(request: httpx.Request) -> httpx.Response:
        # A transient server error is not proof the session was deleted; an
        # ordinary recoverable failure must leave the forwarder running.
        return httpx.Response(503, json={"error": "unavailable"})

    app, server_client = _make_app(handler)
    task = _register_forwarder(session_id)
    try:
        await app.state.reconcile_forwarders_after_reconnect()

        assert not task.cancelled()
        assert session_id in orch._AUTO_FORWARDER_TASKS
    finally:
        await server_client.aclose()
        await _drain(session_id, task)
