"""Regression test: reconnect must reap forwarders for sessions deleted offline.

A session deleted while this runner's tunnel is down takes the server's offline
path, which skips runner-side cleanup that nothing replays on reconnect, so its
restart-forever transcript forwarder survives. The runner now probes each live
forwarder's session on reconnect and, on a definitive 404, reaps it with the
same teardown the delete route uses for a descendant; a transient error or a
still-live (200) session is left untouched.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

import httpx
from fastapi import FastAPI

from omnigent.runner import create_runner_app
from omnigent.runner import subagent_work as sw
from omnigent.runner.native import orchestration as orch


class _FakeAppServer:
    """Stand-in for a native app-server whose close() the reap must await."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


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


async def test_reconnect_fully_reaps_session_deleted_offline() -> None:
    deleted = f"conv_deleted_{uuid.uuid4().hex}"
    alive = f"conv_alive_{uuid.uuid4().hex}"
    grandchild = f"conv_grand_{uuid.uuid4().hex}"

    def handler(request: httpx.Request) -> httpx.Response:
        session_id = request.url.path.rsplit("/", 1)[-1]
        if session_id == deleted:
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json={"id": session_id, "agent_id": "agent_x"})

    app, server_client = _make_app(handler)
    deleted_task = _register_forwarder(deleted)
    alive_task = _register_forwarder(alive)

    # A native server neither forwarder adopted, plus runner-local spawn-family
    # state, must be torn down for the deleted session but left for the live one.
    deleted_server = _FakeAppServer()
    alive_server = _FakeAppServer()
    orch._AUTO_CODEX_APP_SERVERS[deleted] = deleted_server  # type: ignore[assignment]
    orch._AUTO_CODEX_APP_SERVERS[alive] = alive_server  # type: ignore[assignment]
    sw.register_subagent_work(
        parent_session_id=deleted,
        child_session_id=grandchild,
        agent="leaf",
        title="leaf",
        wrapper_label="claude-code-native-ui",
    )

    cleaned: list[str] = []
    registry = app.state.session_resource_registry
    original_cleanup = registry.cleanup_session

    async def _recording_cleanup(session_id: str) -> None:
        cleaned.append(session_id)
        await original_cleanup(session_id)

    registry.cleanup_session = _recording_cleanup  # type: ignore[method-assign]

    try:
        await app.state.reconcile_forwarders_after_reconnect()

        # The deleted session is reaped fully, not just its forwarder: the
        # orphaned native server is closed and its panes/env and spawn-family
        # state are dropped, or the deleted session leaks them after reconnect.
        assert deleted_task.cancelled()
        assert deleted not in orch._AUTO_FORWARDER_TASKS
        assert deleted_server.closed, "native server leaked for a session deleted offline"
        assert deleted not in orch._AUTO_CODEX_APP_SERVERS
        assert deleted in cleaned, "deleted session did not get per-session resource cleanup"
        assert sw.list_subagent_work(deleted) == []
        assert sw.get_subagent_work(grandchild) is None

        # The still-live session keeps its forwarder, native server, and state.
        assert not alive_task.cancelled()
        assert alive in orch._AUTO_FORWARDER_TASKS
        assert not alive_server.closed
        assert alive not in cleaned
    finally:
        registry.cleanup_session = original_cleanup  # type: ignore[method-assign]
        orch._AUTO_CODEX_APP_SERVERS.pop(deleted, None)
        orch._AUTO_CODEX_APP_SERVERS.pop(alive, None)
        sw.unregister_subagent_work_for_session(deleted)
        sw.unregister_child_session(grandchild)
        await server_client.aclose()
        await _drain(deleted, deleted_task)
        await _drain(alive, alive_task)
