"""Owners can rebind an idle session's workspace and host.

Covers PATCH /v1/sessions/{id} and the MCP manage_session update action
(dry-run unless confirm is true), including a never-connected fork and an
ogc_ bot acting for its owner.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from omnigent.db.utils import generate_agent_id
from omnigent.host.frames import (
    HostHelloFrame,
    HostStatFrame,
    HostStopRunnerFrame,
    decode_host_frame,
)
from omnigent.server.auth import LEVEL_EDIT, LEVEL_OWNER
from omnigent.server.host_registry import HostRegistry
from omnigent.server.mcp_endpoint import working_directory_from_labels
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from tests.server.chat_testkit import ANNA, JENS, chat_client, create_account, tools_call
from tests.server.helpers import build_agent_bundle

_ = chat_client

pytestmark = pytest.mark.asyncio

_OLD_PATH = "/old/" + ("a" * 300)
_NEW_PATH = "/new/project"


def _headers(user: str) -> dict[str, str]:
    return {"X-Forwarded-Email": user}


def _composer_labels(path: str) -> dict[str, str]:
    raw = json.dumps(
        {"version": 1, "working_directory": {"path": path}, "worktree": {"mode": "none"}},
        separators=(",", ":"),
    )
    stale = json.dumps({"working_directory": {"path": "/stale"}})
    labels = {"omnigent.composer_context.v1": stale}
    for index, offset in enumerate(range(0, len(raw), 240)):
        labels[f"omnigent.composer_context.v1.{index}"] = raw[offset : offset + 240]
    return labels


def _stores(
    db_uri: str,
) -> tuple[SqlAlchemyConversationStore, SqlAlchemyPermissionStore, HostStore]:
    return (
        SqlAlchemyConversationStore(db_uri),
        SqlAlchemyPermissionStore(db_uri),
        HostStore(db_uri),
    )


def _own(
    permissions: SqlAlchemyPermissionStore,
    user: str,
    session_id: str,
    level: int = LEVEL_OWNER,
) -> None:
    permissions.ensure_user(user)
    permissions.grant(user, session_id, level)


def _host(hosts: HostStore, user: str) -> str:
    host_id = uuid.uuid4().hex
    hosts.upsert_on_connect(host_id, f"laptop-{host_id[:8]}", user)
    return host_id


def _seed(
    db_uri: str,
    *,
    user: str = JENS,
    host_id: str | None,
    workspace: str | None,
    runner_id: str | None = "runner_stale",
    git_branch: str | None = "feature/old",
    level: int = LEVEL_OWNER,
    agent_id: str | None = None,
) -> str:
    conversations, permissions, _hosts = _stores(db_uri)
    labels = _composer_labels(workspace) if workspace else None
    conversation = conversations.create_conversation(
        title="bind me",
        agent_id=agent_id or uuid.uuid4().hex,
        host_id=host_id,
        workspace=workspace,
        runner_id=runner_id,
        git_branch=git_branch,
        labels=labels,
    )
    _own(permissions, user, conversation.id, level)
    return conversation.id


class _FakeWebSocket:
    """Captures outbound frames. The registry only calls send_text."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


@asynccontextmanager
async def _online_host(
    registry: HostRegistry,
    host_id: str,
    replies: Mapping[str, dict[str, Any]] | None = None,
    *,
    hold: asyncio.Event | None = None,
    seen: asyncio.Event | None = None,
) -> AsyncIterator[None]:
    """Register a host that answers host.stat from ``replies`` (default: missing).

    ``seen`` is set when the first stat arrives, before the reply. ``hold``
    pauses every stat until it is set, so a test can start a turn in that window.
    """
    ws = _FakeWebSocket()
    conn = registry.register(
        host_id=host_id,
        ws=ws,  # type: ignore[arg-type]
        hello=HostHelloFrame(version="0.1.0-test", frame_protocol_version=1, name="bind-test"),
        owner=None,
    )

    async def _drain() -> None:
        while True:
            outbound = await conn.outbound_queue.get()
            if outbound is None:
                return
            frame = decode_host_frame(outbound.data)
            if isinstance(frame, HostStopRunnerFrame):
                future = conn.pending_stops.pop(frame.request_id, None)
                if future is not None and not future.done():
                    future.set_result({"status": "stopped", "error": None})
                continue
            if not isinstance(frame, HostStatFrame):
                continue
            if seen is not None and not seen.is_set():
                seen.set()
            if hold is not None:
                await hold.wait()
            reply = (
                dict(replies[frame.path])
                if replies and frame.path in replies
                else {
                    "status": "ok",
                    "exists": False,
                    "type": None,
                    "canonical_path": None,
                    "error": None,
                }
            )
            future = conn.pending_stats.pop(frame.request_id, None)
            if future is not None and not future.done():
                future.set_result(reply)

    task = asyncio.create_task(_drain())
    try:
        yield
    finally:
        conn.outbound_queue.put_nowait(None)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        registry.deregister(host_id)


def _directory(path: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "exists": True,
        "type": "directory",
        "canonical_path": path,
        "error": None,
    }


def _spy_deregister(monkeypatch: pytest.MonkeyPatch, app: FastAPI) -> list[str]:
    """Record tunnel deregistrations. A rebind must not call this."""
    registry = app.state.tunnel_registry
    calls: list[str] = []
    original = registry.deregister

    def wrapped(runner_id: str, session: Any = None) -> Any:
        calls.append(runner_id)
        return original(runner_id, session)

    monkeypatch.setattr(registry, "deregister", wrapped)
    return calls


def _spy_stop(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    """Record host-runner stops the rebind path schedules."""
    calls: list[tuple[str, str, str]] = []

    async def fake(
        session_id: str,
        host_id: str,
        runner_id: str,
        host_registry: Any,
        conversation_store: Any,
    ) -> bool:
        del host_registry, conversation_store
        calls.append((session_id, host_id, runner_id))
        return True

    monkeypatch.setattr(
        "omnigent.server.routes._sessions.orchestration._stop_host_runner_intentionally",
        fake,
    )
    return calls


def _install_agent(db_uri: str, tmp_path: Path, cwd: str) -> str:
    """Register an agent whose ``os_env.cwd`` is the rebind boundary."""
    agent_id = generate_agent_id()
    bundle = build_agent_bundle(
        name=f"cwd-{agent_id[:8]}",
        os_env={"type": "caller_process", "cwd": cwd},
    )
    location = f"{agent_id}/bundle.tar.gz"
    LocalArtifactStore(str(tmp_path / "artifacts")).put(location, bundle)
    SqlAlchemyAgentStore(db_uri).create(
        agent_id=agent_id,
        name=f"cwd-{agent_id[:8]}",
        bundle_location=location,
    )
    return agent_id


def _child(
    db_uri: str,
    parent_id: str,
    *,
    host_id: str,
    workspace: str,
    runner_id: str,
    title: str,
) -> str:
    """Create a sub-agent session. Its runner id is independent of later parent rebinds."""
    conversations, _permissions, _hosts = _stores(db_uri)
    child = conversations.create_conversation(
        title=title,
        parent_conversation_id=parent_id,
        agent_id=uuid.uuid4().hex,
        host_id=host_id,
        workspace=workspace,
        runner_id=runner_id,
    )
    return child.id


def _stored(db_uri: str, session_id: str) -> Any:
    stored = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert stored is not None
    return stored


def _install_hosts(app: FastAPI, db_uri: str) -> HostStore:
    hosts = HostStore(db_uri)
    app.state.host_store = hosts
    return hosts


async def _patch(
    client: httpx.AsyncClient,
    session_id: str,
    body: dict[str, Any],
    *,
    user: str = JENS,
) -> httpx.Response:
    return await client.patch(f"/v1/sessions/{session_id}", json=body, headers=_headers(user))


async def _message(
    client: httpx.AsyncClient, session_id: str, *, user: str = JENS
) -> httpx.Response:
    return await client.post(
        f"/v1/sessions/{session_id}/events",
        json={
            "type": "message",
            "data": {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        },
        headers=_headers(user),
    )


async def test_patch_rebinds_workspace_host_and_composer_label(
    auth_app: FastAPI,
    auth_client: httpx.AsyncClient,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An idle session's column, composer label, and host change; the runner is cleared."""
    hosts = _install_hosts(auth_app, db_uri)
    deregistered = _spy_deregister(monkeypatch, auth_app)
    stopped = _spy_stop(monkeypatch)
    old_host = _host(hosts, JENS)
    new_host = _host(hosts, JENS)
    session_id = _seed(db_uri, host_id=old_host, workspace=_OLD_PATH)
    canonical = "/new/canonical"
    replies = {
        _NEW_PATH: {
            "status": "ok",
            "exists": True,
            "type": "directory",
            "canonical_path": canonical,
            "error": None,
        }
    }
    async with _online_host(auth_app.state.host_registry, new_host, replies):
        response = await _patch(
            auth_client,
            session_id,
            {"workspace": _NEW_PATH, "host_id": new_host, "title": "rebound"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workspace"] == canonical
    assert body["host_id"] == new_host
    assert body["runner_id"] is None
    assert body["title"] == "rebound"

    stored = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert stored is not None
    assert stored.workspace == canonical
    assert stored.host_id == new_host
    assert stored.runner_id is None
    assert stored.git_branch is None
    assert working_directory_from_labels(stored.labels) == canonical
    assert "omnigent.composer_context.v1" not in stored.labels
    assert "omnigent.composer_context.v1.1" not in stored.labels
    assert deregistered == []
    assert stopped == [(session_id, old_host, "runner_stale")]


async def test_same_binding_keeps_the_runner(
    auth_app: FastAPI,
    auth_client: httpx.AsyncClient,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeating the stored host and workspace does not drop the idle runner."""
    hosts = _install_hosts(auth_app, db_uri)
    deregistered = _spy_deregister(monkeypatch, auth_app)
    stopped = _spy_stop(monkeypatch)
    host_id = _host(hosts, JENS)
    session_id = _seed(db_uri, host_id=host_id, workspace="/work/app", runner_id="runner_keep")
    async with _online_host(
        auth_app.state.host_registry, host_id, {"/work/app": _directory("/work/app")}
    ):
        response = await _patch(
            auth_client, session_id, {"workspace": "/work/app", "host_id": host_id}
        )
    assert response.status_code == 200, response.text
    assert deregistered == []
    assert stopped == []
    stored = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert stored is not None
    assert stored.runner_id == "runner_keep"
    assert stored.git_branch == "feature/old"
    assert working_directory_from_labels(stored.labels) == "/work/app"


async def test_running_session_conflicts(
    auth_app: FastAPI,
    auth_client: httpx.AsyncClient,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A running session cannot change workspace or host, and the row stays put."""
    hosts = _install_hosts(auth_app, db_uri)
    deregistered = _spy_deregister(monkeypatch, auth_app)
    stopped = _spy_stop(monkeypatch)
    host_id = _host(hosts, JENS)
    other = _host(hosts, JENS)
    session_id = _seed(db_uri, host_id=host_id, workspace="/work/app")
    SqlAlchemyConversationStore(db_uri).set_session_live_status(session_id, "running")
    response = await _patch(
        auth_client, session_id, {"workspace": "/work/elsewhere", "host_id": other}
    )
    assert response.status_code == 409, response.text
    assert "running" in response.text
    stored = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert stored is not None
    assert stored.workspace == "/work/app"
    assert stored.host_id == host_id
    assert stored.runner_id == "runner_stale"
    assert deregistered == []
    assert stopped == []


async def test_foreign_host_is_forbidden_and_unknown_host_is_missing(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    """Another user's host is 403; a well-formed unknown id is 404."""
    hosts = _install_hosts(auth_app, db_uri)
    own = _host(hosts, JENS)
    foreign = _host(hosts, ANNA)
    session_id = _seed(db_uri, host_id=own, workspace="/work/app")
    forbidden = await _patch(auth_client, session_id, {"host_id": foreign})
    assert forbidden.status_code == 403, forbidden.text
    assert "not your host" in forbidden.text
    missing = await _patch(auth_client, session_id, {"host_id": uuid.uuid4().hex})
    assert missing.status_code == 404, missing.text
    assert "host not found" in missing.text
    malformed = await _patch(auth_client, session_id, {"host_id": "not-a-host"})
    assert malformed.status_code == 404, malformed.text
    stored = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert stored is not None
    assert stored.host_id == own
    assert stored.workspace == "/work/app"


@pytest.mark.parametrize(
    "workspace",
    ["relative/path", "foo", "~/projects", " /tmp/padded", "", "C:not-absolute"],
)
async def test_relative_workspace_is_unprocessable(
    auth_client: httpx.AsyncClient, db_uri: str, workspace: str
) -> None:
    """A path that is not absolute on the host is rejected before any write."""
    session_id = _seed(db_uri, host_id=None, workspace=None, runner_id=None, git_branch=None)
    response = await _patch(auth_client, session_id, {"workspace": workspace})
    assert response.status_code == 422, response.text
    assert "absolute" in response.text


async def test_explicit_null_does_not_unbind(auth_client: httpx.AsyncClient, db_uri: str) -> None:
    """Explicit JSON null is rejected with 422. Omit the field to leave it."""
    session_id = _seed(
        db_uri, host_id=None, workspace="/work/app", runner_id=None, git_branch=None
    )
    response = await _patch(auth_client, session_id, {"workspace": None, "host_id": None})
    assert response.status_code == 422, response.text


async def test_missing_path_on_online_host_is_unprocessable(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    """An online host that reports the path missing yields 400 and no write."""
    hosts = _install_hosts(auth_app, db_uri)
    host_id = _host(hosts, JENS)
    session_id = _seed(db_uri, host_id=host_id, workspace="/work/app")
    missing = "/missing/project"
    async with _online_host(auth_app.state.host_registry, host_id):
        response = await _patch(auth_client, session_id, {"workspace": missing})
    assert response.status_code == 400, response.text
    assert f"workspace path does not exist on host 'laptop-{host_id[:8]}': {missing}" in (
        response.text
    )
    stored = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert stored is not None
    assert stored.workspace == "/work/app"
    assert stored.runner_id == "runner_stale"


async def test_non_directory_on_online_host_is_unprocessable(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    """A file path on an online host is not a workspace."""
    hosts = _install_hosts(auth_app, db_uri)
    host_id = _host(hosts, JENS)
    session_id = _seed(db_uri, host_id=host_id, workspace="/work/app")
    path = "/work/README.md"
    replies = {
        path: {
            "status": "ok",
            "exists": True,
            "type": "file",
            "canonical_path": path,
            "error": None,
        }
    }
    async with _online_host(auth_app.state.host_registry, host_id, replies):
        response = await _patch(auth_client, session_id, {"workspace": path})
    assert response.status_code == 400, response.text
    assert "not a directory" in response.text


async def test_offline_host_conflicts(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    """A host that is offline cannot accept a rebind, same as session create."""
    hosts = _install_hosts(auth_app, db_uri)
    host_id = _host(hosts, JENS)
    hosts.set_offline(host_id)
    session_id = _seed(db_uri, host_id=None, workspace="/work/app", runner_id="runner_stale")
    response = await _patch(
        auth_client, session_id, {"workspace": "/work/next", "host_id": host_id}
    )
    assert response.status_code == 409, response.text
    assert "offline" in response.text
    stored = _stored(db_uri, session_id)
    assert stored.workspace == "/work/app"
    assert stored.host_id is None
    assert stored.runner_id == "runner_stale"


async def test_host_without_workspace_is_unprocessable(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    """Binding a host onto a session that has no workspace requires one."""
    hosts = _install_hosts(auth_app, db_uri)
    host_id = _host(hosts, JENS)
    session_id = _seed(db_uri, host_id=None, workspace=None, runner_id=None, git_branch=None)
    response = await _patch(auth_client, session_id, {"host_id": host_id})
    assert response.status_code == 400, response.text
    assert "workspace required when host_id is set" in response.text


async def test_editor_cannot_rebind(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    """Workspace and host changes are owner-only."""
    hosts = _install_hosts(auth_app, db_uri)
    host_id = _host(hosts, JENS)
    session_id = _seed(
        db_uri, host_id=host_id, workspace="/work/app", level=LEVEL_EDIT, runner_id=None
    )
    response = await _patch(auth_client, session_id, {"workspace": "/work/elsewhere"})
    assert response.status_code == 403, response.text
    stored = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert stored is not None
    assert stored.workspace == "/work/app"


async def test_mcp_editor_cannot_rebind(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    """manage_session update uses the PATCH owner gate for workspace."""
    hosts = _install_hosts(auth_app, db_uri)
    host_id = _host(hosts, JENS)
    session_id = _seed(
        db_uri, host_id=host_id, workspace="/work/app", level=LEVEL_EDIT, runner_id=None
    )
    denied = _tool_text(
        await _manage(
            auth_client,
            session_id,
            {"workspace": "/work/elsewhere", "confirm": True},
        )
    )
    assert "owner permission" in denied
    stored = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert stored is not None
    assert stored.workspace == "/work/app"


async def test_fork_without_runner_can_be_bound(
    auth_app: FastAPI,
    auth_client: httpx.AsyncClient,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A never-connected fork binds, and send_message no longer says no runner is bound."""
    hosts = _install_hosts(auth_app, db_uri)
    source_host = _host(hosts, JENS)
    bound_host = _host(hosts, JENS)
    conversations, permissions, _hosts = _stores(db_uri)
    source = conversations.create_conversation(
        title="source",
        agent_id=uuid.uuid4().hex,
        host_id=source_host,
        workspace="/src/app",
    )
    fork = conversations.fork_conversation(source.id)
    assert fork.host_id is None
    assert fork.runner_id is None
    assert fork.workspace is None
    _own(permissions, JENS, fork.id)

    before = await _message(auth_client, fork.id)
    assert before.status_code == 503, before.text
    assert "No runner bound for session" in before.text

    async with _online_host(
        auth_app.state.host_registry, bound_host, {"/src/fork": _directory("/src/fork")}
    ):
        bound = await _patch(
            auth_client, fork.id, {"workspace": "/src/fork", "host_id": bound_host}
        )
    assert bound.status_code == 200, bound.text
    hosts.set_offline(bound_host)
    stored = conversations.get_conversation(fork.id)
    assert stored is not None
    assert stored.host_id == bound_host
    assert stored.workspace == "/src/fork"
    assert stored.runner_id is None

    monkeypatch.setattr("omnigent.server.routes.sessions._HOST_RECONNECT_GRACE_S", 0)
    after = await _message(auth_client, fork.id)
    assert after.status_code == 503, after.text
    assert "No runner bound for session" not in after.text
    assert bound_host in after.text


def _tool_text(response: httpx.Response) -> Any:
    payload = response.json()
    assert "error" not in payload, payload
    result = payload["result"]
    text = result["content"][0]["text"]
    if result.get("isError") is True:
        return text
    return json.loads(text)


async def _manage(
    client: httpx.AsyncClient,
    session_id: str,
    arguments: dict[str, Any],
    *,
    user: str | None = JENS,
    token: str | None = None,
) -> httpx.Response:
    args = {"action": "update", "session_id": session_id, **arguments}
    if token is not None:
        return await tools_call(client, "manage_session", args, token=token)
    assert user is not None
    return await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "manage_session", "arguments": args},
        },
        headers=_headers(user),
    )


async def test_mcp_dry_run_previews_and_confirm_persists(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    """manage_session update previews a rebind until confirm is true."""
    hosts = _install_hosts(auth_app, db_uri)
    old_host = _host(hosts, JENS)
    new_host = _host(hosts, JENS)
    session_id = _seed(db_uri, host_id=old_host, workspace="/work/app")

    preview = _tool_text(
        await _manage(
            auth_client,
            session_id,
            {"workspace": "/work/next", "host_id": new_host},
        )
    )
    assert preview["dry_run"] is True
    assert preview["method"] == "PATCH"
    assert preview["body"] == {"workspace": "/work/next", "host_id": new_host}
    assert "rebind" in preview["effect"]
    untouched = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert untouched is not None
    assert untouched.workspace == "/work/app"
    assert untouched.host_id == old_host
    assert untouched.runner_id == "runner_stale"

    async with _online_host(
        auth_app.state.host_registry, new_host, {"/work/next": _directory("/work/next")}
    ):
        confirmed = _tool_text(
            await _manage(
                auth_client,
                session_id,
                {"workspace": "/work/next", "host_id": new_host, "confirm": True},
            )
        )
    assert confirmed["workspace"] == "/work/next"
    assert confirmed["host_id"] == new_host
    assert confirmed["runner_id"] is None
    stored = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert stored is not None
    assert stored.workspace == "/work/next"
    assert working_directory_from_labels(stored.labels) == "/work/next"


async def test_mcp_confirm_surfaces_bind_errors(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    """409, 403, and 422 from the route are tool errors and do not persist."""
    hosts = _install_hosts(auth_app, db_uri)
    host_id = _host(hosts, JENS)
    foreign = _host(hosts, ANNA)
    session_id = _seed(db_uri, host_id=host_id, workspace="/work/app")
    SqlAlchemyConversationStore(db_uri).set_session_live_status(session_id, "waiting")

    running = _tool_text(
        await _manage(
            auth_client,
            session_id,
            {"workspace": "/work/next", "confirm": True},
        )
    )
    assert "running" in running

    SqlAlchemyConversationStore(db_uri).set_session_live_status(session_id, "idle")
    forbidden = _tool_text(
        await _manage(
            auth_client,
            session_id,
            {"host_id": foreign, "confirm": True},
        )
    )
    assert "not your host" in forbidden
    missing_path = "/no/such/dir"
    async with _online_host(auth_app.state.host_registry, host_id):
        missing = _tool_text(
            await _manage(
                auth_client,
                session_id,
                {"workspace": missing_path, "confirm": True},
            )
        )
    assert missing_path in missing
    assert "does not exist" in missing
    stored = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert stored is not None
    assert stored.workspace == "/work/app"
    assert stored.host_id == host_id
    assert stored.runner_id == "runner_stale"


async def test_bot_token_rebinds_for_its_owner(
    chat_client: httpx.AsyncClient, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ogc_ account can rebind a session its owner could rebind, and not a foreign host."""
    recorded: list[dict[str, Any]] = []

    def capture(**attrs: object) -> None:
        recorded.append(dict(attrs))

    monkeypatch.setattr("omnigent.server.routes._session_bind.add_audit_attrs", capture)
    app = chat_client._transport.app  # type: ignore[attr-defined]
    assert isinstance(app, FastAPI)
    hosts = _install_hosts(app, db_uri)
    host_id = _host(hosts, JENS)
    foreign = _host(hosts, ANNA)
    session_id = _seed(
        db_uri, host_id=None, workspace="/work/app", runner_id=None, git_branch=None
    )
    created = await create_account(chat_client, JENS, "bind-bot")
    assert created.status_code == 201, created.text
    token = created.json()["token"]

    preview = _tool_text(
        await _manage(
            chat_client,
            session_id,
            {"workspace": "/work/bot", "host_id": host_id},
            token=token,
            user=None,
        )
    )
    assert preview["dry_run"] is True
    preview_row = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert preview_row is not None
    assert preview_row.workspace == "/work/app"

    async with _online_host(
        app.state.host_registry, host_id, {"/work/bot": _directory("/work/bot")}
    ):
        confirmed = _tool_text(
            await _manage(
                chat_client,
                session_id,
                {"workspace": "/work/bot", "host_id": host_id, "confirm": True},
                token=token,
                user=None,
            )
        )
    assert confirmed["workspace"] == "/work/bot"
    assert confirmed["host_id"] == host_id
    stored = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert stored is not None
    assert stored.workspace == "/work/bot"
    assert stored.host_id == host_id
    assert working_directory_from_labels(stored.labels) == "/work/bot"
    assert recorded
    audit = recorded[-1]
    assert audit["event"] == "session_rebind"
    assert str(audit["mcp_actor"]).startswith("agent:")
    assert audit["owner"] == JENS
    assert audit["old_host_id"] is None
    assert audit["new_host_id"] == host_id
    assert audit["old_workspace"] == "/work/app"
    assert audit["new_workspace"] == "/work/bot"

    denied = _tool_text(
        await _manage(
            chat_client,
            session_id,
            {"host_id": foreign, "confirm": True},
            token=token,
            user=None,
        )
    )
    assert "not your host" in denied


async def _rebind_while_stat_is_held(
    app: FastAPI,
    client: httpx.AsyncClient,
    session_id: str,
    host_id: str,
    workspace: str,
    mutate: Callable[[], None],
) -> httpx.Response:
    """Start a rebind, run ``mutate`` once the host stat is in flight, then release it."""
    seen = asyncio.Event()
    hold = asyncio.Event()
    replies = {workspace: _directory(workspace)}
    async with _online_host(app.state.host_registry, host_id, replies, hold=hold, seen=seen):
        task = asyncio.create_task(
            _patch(client, session_id, {"workspace": workspace, "host_id": host_id})
        )
        try:
            await asyncio.wait_for(seen.wait(), timeout=5)
            mutate()
            hold.set()
            return await asyncio.wait_for(task, timeout=5)
        finally:
            hold.set()
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


async def test_rebind_loses_to_a_turn_that_starts_during_the_host_stat(
    auth_app: FastAPI,
    auth_client: httpx.AsyncClient,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A turn that becomes running while host.stat is in flight keeps its runner."""
    hosts = _install_hosts(auth_app, db_uri)
    deregistered = _spy_deregister(monkeypatch, auth_app)
    stopped = _spy_stop(monkeypatch)
    host_id = _host(hosts, JENS)
    session_id = _seed(db_uri, host_id=host_id, workspace="/work/app", runner_id="runner_live")
    store = SqlAlchemyConversationStore(db_uri)

    response = await _rebind_while_stat_is_held(
        auth_app,
        auth_client,
        session_id,
        host_id,
        "/work/moved",
        lambda: store.set_session_live_status(session_id, "running"),
    )
    assert response.status_code == 409, response.text
    assert "running" in response.text
    stored = _stored(db_uri, session_id)
    assert stored.workspace == "/work/app"
    assert stored.host_id == host_id
    assert stored.runner_id == "runner_live"
    assert stored.live_status == "running"
    assert deregistered == []
    assert stopped == []


async def test_rebind_loses_when_the_runner_changes_during_the_host_stat(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    """The compare-and-set refuses when the locked runner id is no longer the one we saw."""
    hosts = _install_hosts(auth_app, db_uri)
    host_id = _host(hosts, JENS)
    session_id = _seed(db_uri, host_id=host_id, workspace="/work/app", runner_id="runner_old")
    store = SqlAlchemyConversationStore(db_uri)

    def swap_runner() -> None:
        store.replace_runner_id(session_id, "runner_new")

    response = await _rebind_while_stat_is_held(
        auth_app,
        auth_client,
        session_id,
        host_id,
        "/work/moved",
        swap_runner,
    )
    assert response.status_code == 409, response.text
    assert "running" in response.text
    stored = _stored(db_uri, session_id)
    assert stored.workspace == "/work/app"
    assert stored.runner_id == "runner_new"


async def test_running_child_sharing_the_runner_blocks_the_rebind(
    auth_app: FastAPI,
    auth_client: httpx.AsyncClient,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A descendant in a turn on the shared runner id keeps both runner ids."""
    hosts = _install_hosts(auth_app, db_uri)
    deregistered = _spy_deregister(monkeypatch, auth_app)
    stopped = _spy_stop(monkeypatch)
    host_id = _host(hosts, JENS)
    session_id = _seed(db_uri, host_id=host_id, workspace="/work/app", runner_id="runner_shared")
    child_id = _child(
        db_uri,
        session_id,
        host_id=host_id,
        workspace="/work/app",
        runner_id="runner_shared",
        title="worker:busy",
    )
    SqlAlchemyConversationStore(db_uri).set_session_live_status(child_id, "running")
    async with _online_host(
        auth_app.state.host_registry, host_id, {"/work/next": _directory("/work/next")}
    ):
        response = await _patch(
            auth_client, session_id, {"workspace": "/work/next", "host_id": host_id}
        )
    assert response.status_code == 409, response.text
    assert "sharing its runner" in response.text
    parent = _stored(db_uri, session_id)
    child = _stored(db_uri, child_id)
    assert parent.runner_id == "runner_shared"
    assert parent.workspace == "/work/app"
    assert child.runner_id == "runner_shared"
    assert child.live_status == "running"
    assert deregistered == []
    assert stopped == []


async def test_idle_child_keeps_the_shared_runner(
    auth_app: FastAPI,
    auth_client: httpx.AsyncClient,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An idle child keeps the runner id. The parent drops its copy and does not stop it."""
    hosts = _install_hosts(auth_app, db_uri)
    deregistered = _spy_deregister(monkeypatch, auth_app)
    stopped = _spy_stop(monkeypatch)
    host_id = _host(hosts, JENS)
    session_id = _seed(db_uri, host_id=host_id, workspace="/work/app", runner_id="runner_shared")
    child_id = _child(
        db_uri,
        session_id,
        host_id=host_id,
        workspace="/work/app",
        runner_id="runner_shared",
        title="worker:idle",
    )
    async with _online_host(
        auth_app.state.host_registry, host_id, {"/work/next": _directory("/work/next")}
    ):
        response = await _patch(
            auth_client, session_id, {"workspace": "/work/next", "host_id": host_id}
        )
    assert response.status_code == 200, response.text
    parent = _stored(db_uri, session_id)
    child = _stored(db_uri, child_id)
    assert parent.runner_id is None
    assert parent.workspace == "/work/next"
    assert parent.git_branch is None
    assert child.runner_id == "runner_shared"
    assert child.workspace == "/work/app"
    assert deregistered == []
    assert stopped == []


async def test_running_child_on_a_different_runner_does_not_block(
    auth_app: FastAPI,
    auth_client: httpx.AsyncClient,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A busy descendant on its own runner does not pin the parent's runner."""
    hosts = _install_hosts(auth_app, db_uri)
    stopped = _spy_stop(monkeypatch)
    host_id = _host(hosts, JENS)
    session_id = _seed(db_uri, host_id=host_id, workspace="/work/app", runner_id="runner_parent")
    child_id = _child(
        db_uri,
        session_id,
        host_id=host_id,
        workspace="/work/app",
        runner_id="runner_child",
        title="worker:other",
    )
    SqlAlchemyConversationStore(db_uri).set_session_live_status(child_id, "running")
    async with _online_host(
        auth_app.state.host_registry, host_id, {"/work/next": _directory("/work/next")}
    ):
        response = await _patch(
            auth_client, session_id, {"workspace": "/work/next", "host_id": host_id}
        )
    assert response.status_code == 200, response.text
    parent = _stored(db_uri, session_id)
    child = _stored(db_uri, child_id)
    assert parent.runner_id is None
    assert child.runner_id == "runner_child"
    assert child.live_status == "running"
    assert stopped == [(session_id, host_id, "runner_parent")]


async def test_workspace_outside_the_agent_boundary_is_rejected(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str, tmp_path: Path
) -> None:
    """A restricted agent's session cannot move to /, ~/.ssh, or ~/.omnigent."""
    hosts = _install_hosts(auth_app, db_uri)
    host_id = _host(hosts, JENS)
    boundary = "/projects/app"
    agent_id = _install_agent(db_uri, tmp_path, boundary)
    session_id = _seed(
        db_uri, host_id=host_id, workspace=boundary, runner_id="runner_stale", agent_id=agent_id
    )
    outside = ("/", "/home/user/.ssh", "/home/user/.omnigent")
    replies = {boundary: _directory(boundary)}
    replies.update({path: _directory(path) for path in outside})
    async with _online_host(auth_app.state.host_registry, host_id, replies):
        tilde = await _patch(auth_client, session_id, {"workspace": "~/.ssh"})
        assert tilde.status_code == 422, tilde.text
        for path in outside:
            response = await _patch(auth_client, session_id, {"workspace": path})
            assert response.status_code == 400, response.text
            assert "outside" in response.text
            assert boundary in response.text
    stored = _stored(db_uri, session_id)
    assert stored.workspace == boundary
    assert stored.runner_id == "runner_stale"


async def test_relative_subdir_must_exist_on_the_host(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str, tmp_path: Path
) -> None:
    """``cwd: ./widget`` still requires that subdirectory under the new workspace."""
    hosts = _install_hosts(auth_app, db_uri)
    host_id = _host(hosts, JENS)
    agent_id = _install_agent(db_uri, tmp_path, "./widget")
    session_id = _seed(
        db_uri,
        host_id=host_id,
        workspace="/work/old",
        runner_id=None,
        git_branch=None,
        agent_id=agent_id,
    )
    workspace = "/work/next"
    replies = {workspace: _directory(workspace)}
    async with _online_host(auth_app.state.host_registry, host_id, replies):
        response = await _patch(auth_client, session_id, {"workspace": workspace})
    assert response.status_code == 400, response.text
    assert "widget" in response.text
    assert _stored(db_uri, session_id).workspace == "/work/old"


async def test_symlink_is_judged_by_the_canonical_path(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str, tmp_path: Path
) -> None:
    """A symlink whose real path leaves the boundary is rejected."""
    hosts = _install_hosts(auth_app, db_uri)
    host_id = _host(hosts, JENS)
    boundary = "/work"
    agent_id = _install_agent(db_uri, tmp_path, boundary)
    session_id = _seed(
        db_uri, host_id=host_id, workspace="/work/app", runner_id=None, agent_id=agent_id
    )
    link = "/work/link"
    replies = {
        boundary: _directory(boundary),
        "/work/app": _directory("/work/app"),
        link: _directory("/secret"),
    }
    async with _online_host(auth_app.state.host_registry, host_id, replies):
        response = await _patch(auth_client, session_id, {"workspace": link})
    assert response.status_code == 400, response.text
    assert "outside" in response.text
    assert _stored(db_uri, session_id).workspace == "/work/app"


async def test_dotdot_is_stored_as_the_canonical_path(
    auth_app: FastAPI,
    auth_client: httpx.AsyncClient,
    db_uri: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``..`` is stored as the host's canonical path when that path stays inside the boundary."""
    hosts = _install_hosts(auth_app, db_uri)
    deregistered = _spy_deregister(monkeypatch, auth_app)
    host_id = _host(hosts, JENS)
    boundary = "/work"
    agent_id = _install_agent(db_uri, tmp_path, boundary)
    session_id = _seed(
        db_uri,
        host_id=host_id,
        workspace="/work/app",
        runner_id="runner_stale",
        agent_id=agent_id,
    )
    requested = "/work/sub/../inside"
    canonical = "/work/inside"
    replies = {
        boundary: _directory(boundary),
        requested: _directory(canonical),
    }
    async with _online_host(auth_app.state.host_registry, host_id, replies):
        response = await _patch(auth_client, session_id, {"workspace": requested})
    assert response.status_code == 200, response.text
    assert response.json()["workspace"] == canonical
    stored = _stored(db_uri, session_id)
    assert stored.workspace == canonical
    assert stored.runner_id is None
    assert working_directory_from_labels(stored.labels) == canonical
    assert deregistered == []


async def test_admin_can_rebind_another_users_session_onto_their_own_host(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    """An admin passes the session gate, and the host must still belong to that admin."""
    hosts = _install_hosts(auth_app, db_uri)
    owner_host = _host(hosts, JENS)
    admin_host = _host(hosts, ANNA)
    session_id = _seed(db_uri, host_id=owner_host, workspace="/work/app", runner_id=None)
    permissions = SqlAlchemyPermissionStore(db_uri)
    permissions.ensure_user(ANNA)
    permissions.set_admin(ANNA, True)
    replies = {
        "/anna/work": _directory("/anna/work"),
        "/work/app": _directory("/work/app"),
    }
    async with _online_host(auth_app.state.host_registry, admin_host, replies):
        allowed = await _patch(
            auth_client,
            session_id,
            {"workspace": "/anna/work", "host_id": admin_host},
            user=ANNA,
        )
    assert allowed.status_code == 200, allowed.text
    stored = _stored(db_uri, session_id)
    assert stored.host_id == admin_host
    assert stored.workspace == "/anna/work"
    async with _online_host(auth_app.state.host_registry, owner_host, replies):
        denied = await _patch(
            auth_client,
            session_id,
            {"workspace": "/work/app", "host_id": owner_host},
            user=ANNA,
        )
    assert denied.status_code == 403, denied.text
    assert "not your host" in denied.text
    stayed = _stored(db_uri, session_id)
    assert stayed.host_id == admin_host
    assert stayed.workspace == "/anna/work"


async def test_archived_session_conflicts(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    """An archived session cannot change workspace or host until it is unarchived."""
    hosts = _install_hosts(auth_app, db_uri)
    host_id = _host(hosts, JENS)
    session_id = _seed(db_uri, host_id=host_id, workspace="/work/app")
    SqlAlchemyConversationStore(db_uri).update_conversation(session_id, archived=True)
    response = await _patch(auth_client, session_id, {"workspace": "/work/next"})
    assert response.status_code == 409, response.text
    assert "archived" in response.text
    stored = _stored(db_uri, session_id)
    assert stored.workspace == "/work/app"
    assert stored.runner_id == "runner_stale"
    assert stored.archived is True


async def test_host_on_another_replica_is_wrong_replica(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    """A live host whose tunnel is not on this replica is wrong_replica, and nothing is written."""
    hosts = _install_hosts(auth_app, db_uri)
    host_id = _host(hosts, JENS)
    session_id = _seed(db_uri, host_id=host_id, workspace="/work/app")
    response = await _patch(auth_client, session_id, {"workspace": "/work/next"})
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error"]["code"] == "wrong_replica"
    assert "another replica" in body["error"]["message"]
    stored = _stored(db_uri, session_id)
    assert stored.workspace == "/work/app"
    assert stored.runner_id == "runner_stale"


async def test_workspace_only_rebind_without_a_host(
    auth_app: FastAPI,
    auth_client: httpx.AsyncClient,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A session with no host stores an absolute path and does not stop or deregister a tunnel."""
    _install_hosts(auth_app, db_uri)
    deregistered = _spy_deregister(monkeypatch, auth_app)
    stopped = _spy_stop(monkeypatch)
    recorded: list[dict[str, Any]] = []

    def capture(**attrs: object) -> None:
        recorded.append(dict(attrs))

    monkeypatch.setattr("omnigent.server.routes._session_bind.add_audit_attrs", capture)
    session_id = _seed(
        db_uri,
        host_id=None,
        workspace="/work/old",
        runner_id="runner_stale",
        git_branch="feature/old",
    )
    response = await _patch(auth_client, session_id, {"workspace": "/work/new"})
    assert response.status_code == 200, response.text
    stored = _stored(db_uri, session_id)
    assert stored.workspace == "/work/new"
    assert stored.host_id is None
    assert stored.runner_id is None
    assert stored.git_branch is None
    assert working_directory_from_labels(stored.labels) == "/work/new"
    assert deregistered == []
    assert stopped == []
    assert recorded[-1]["event"] == "session_rebind"
    assert recorded[-1]["owner"] == JENS
    assert recorded[-1]["mcp_actor"] is None
    assert recorded[-1]["old_workspace"] == "/work/old"
    assert recorded[-1]["new_workspace"] == "/work/new"
    assert recorded[-1]["old_host_id"] is None
    assert recorded[-1]["new_host_id"] is None


async def test_bot_hook_can_refuse_a_rebind(
    chat_client: httpx.AsyncClient, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``bot_may_rebind`` returning false is 403 and writes nothing."""
    app = chat_client._transport.app  # type: ignore[attr-defined]
    assert isinstance(app, FastAPI)
    hosts = _install_hosts(app, db_uri)
    host_id = _host(hosts, JENS)
    session_id = _seed(db_uri, host_id=host_id, workspace="/work/app")
    created = await create_account(chat_client, JENS, "bind-bot-denied")
    assert created.status_code == 201, created.text

    def refuse(**_kwargs: object) -> bool:
        return False

    monkeypatch.setattr("omnigent.server.routes._session_bind.bot_may_rebind", refuse)
    denied = _tool_text(
        await _manage(
            chat_client,
            session_id,
            {"workspace": "/work/next", "confirm": True},
            token=created.json()["token"],
            user=None,
        )
    )
    assert "cannot rebind" in denied
    stored = _stored(db_uri, session_id)
    assert stored.workspace == "/work/app"
    assert stored.runner_id == "runner_stale"
