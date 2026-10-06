"""Regression test: a failed native sub-agent spawn must not leak the child forwarder.

When ``sys_session_send`` spawns a native sub-agent, binding the child launches
its harness and registers a restart-forever transcript forwarder on the runner
(``orchestration._AUTO_FORWARDER_TASKS[child]``). If the child's first-turn
message POST then fails, ``_teardown_failed_child`` unregisters the runner-local
work mappings and DELETEs the child **on the server** — but it never cancels the
runner-local forwarder. It relies on that server delete propagating back to the
runner's ``delete_session`` route over the reverse tunnel to do the cancel.

When the tunnel is down, that reverse DELETE never arrives, so the child's
forwarder survives and keeps POSTing transcript events to a session the server
has already deleted.

The ``httpx.MockTransport`` here stands in for the server with no reverse tunnel
to the runner (it records the DELETE but cannot drive the runner route), and the
pre-registered forwarder stands in for the native pane the bind would have
launched.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from types import SimpleNamespace

import httpx
import pytest

from omnigent.runner import subagent_work
from omnigent.runner.native import orchestration as orch
from omnigent.runner.tool_dispatch import execute_tool

PARENT_ID = "conv_parent"
CHILD_ID = "conv_child_leaked"


@pytest.mark.asyncio
async def test_failed_spawn_cancels_child_forwarder() -> None:
    deletes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        if method == "GET" and path == f"/v1/sessions/{PARENT_ID}":
            return httpx.Response(
                200,
                json={
                    "id": PARENT_ID,
                    "agent_id": "agent_parent",
                    "root_conversation_id": PARENT_ID,
                    "parent_session_id": None,
                },
            )
        # No pre-existing child for this (parent, agent, title): proceed to create.
        if method == "GET" and path == f"/v1/sessions/{PARENT_ID}/child_sessions":
            return httpx.Response(200, json={"data": []})
        if method == "POST" and path == "/v1/sessions":
            return httpx.Response(
                200,
                json={
                    "id": CHILD_ID,
                    "session_id": CHILD_ID,
                    "labels": {"omnigent.wrapper": "claude-code-native-ui"},
                },
            )
        # THE FAULT: the child's first-turn message POST fails.
        if method == "POST" and path == f"/v1/sessions/{CHILD_ID}/events":
            return httpx.Response(500, json={"error": "boom"})
        # Reverse-tunnel-less server delete: recorded, but cannot drive the
        # runner's delete_session route that would cancel the forwarder.
        if method == "DELETE" and path.startswith("/v1/sessions/"):
            deletes.append(path.rsplit("/", 1)[-1])
            return httpx.Response(200, json={"deleted": True})
        return httpx.Response(404, json={"error": f"unmocked {method} {path}"})

    # The native pane bind would have launched the harness and registered this
    # restart-forever transcript forwarder for the child.
    forwarder: asyncio.Task[object] = asyncio.ensure_future(asyncio.sleep(3600))
    forwarder.set_name(f"claude-forwarder-{CHILD_ID}")
    orch._register_auto_forwarder_task(CHILD_ID, forwarder)

    inbox: asyncio.Queue = asyncio.Queue()
    subagent_work._session_inboxes_ref[PARENT_ID] = inbox
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://server"
        ) as server_client:
            output = await execute_tool(
                tool_name="sys_session_send",
                arguments=json.dumps(
                    {"agent": "researcher", "title": "task-1", "args": "do the thing"}
                ),
                server_client=server_client,
                conversation_id=PARENT_ID,
                agent_spec=SimpleNamespace(sub_agents=[SimpleNamespace(name="researcher")]),
                session_inbox=inbox,
            )

        assert isinstance(output, str) and output.startswith("Error"), (
            f"a failed child-message post must return a handled error (got {output!r})"
        )
        assert CHILD_ID in deletes, "teardown must delete the created child server-side"

        # The teardown deleted the server child but must also cancel the
        # runner-local forwarder; otherwise it keeps POSTing events to a
        # session the server has deleted once the reverse tunnel is down.
        assert forwarder.cancelled() or CHILD_ID not in orch._AUTO_FORWARDER_TASKS, (
            "failed spawn leaked the child's transcript forwarder: teardown relied "
            "on a reverse-tunnel DELETE that never cancels it"
        )
    finally:
        subagent_work._session_inboxes_ref.pop(PARENT_ID, None)
        subagent_work.unregister_child_session(CHILD_ID)
        subagent_work.unregister_subagent_work(CHILD_ID)
        orch._AUTO_FORWARDER_TASKS.pop(CHILD_ID, None)
        if not forwarder.done():
            forwarder.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await forwarder
