"""Owners can rebind an idle session's workspace and host.

Covers PATCH /v1/sessions/{id} and the MCP manage_session update action
(dry-run unless confirm is true), including a never-connected fork and an
ogc_ bot acting for its owner.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from omnigent.host.frames import HostHelloFrame, HostStatFrame, decode_host_frame
from omnigent.server.auth import LEVEL_EDIT, LEVEL_OWNER
from omnigent.server.host_registry import HostRegistry
from omnigent.server.mcp_endpoint import working_directory_from_labels
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from tests.server.chat_testkit import ANNA, JENS, chat_client, create_account, tools_call

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
) -> str:
    conversations, permissions, _hosts = _stores(db_uri)
    labels = _composer_labels(workspace) if workspace else None
    conversation = conversations.create_conversation(
        title="bind me",
        agent_id=uuid.uuid4().hex,
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
) -> AsyncIterator[None]:
    """Register a host that answers host.stat from ``replies`` (default: missing)."""
    ws = _FakeWebSocket()
    conn = registry.register(
        host_id=host_id,
        ws=ws,  # type: ignore[arg-type]
        hello=HostHelloFrame(version="0.1.0-test", frame_protocol_version=1, name="bind-test"),
        owner=None,
    )

    async def _drain() -> None:
        while True:
            frame_text = await conn.outbound_queue.get()
            if frame_text is None:
                return
            frame = decode_host_frame(frame_text)
            if not isinstance(frame, HostStatFrame):
                continue
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
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    """An idle session's column, composer label, and host change; the runner is cleared."""
    hosts = _install_hosts(auth_app, db_uri)
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


async def test_same_binding_keeps_the_runner(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    """Repeating the stored host and workspace does not drop the idle runner."""
    hosts = _install_hosts(auth_app, db_uri)
    host_id = _host(hosts, JENS)
    session_id = _seed(db_uri, host_id=host_id, workspace="/work/app", runner_id="runner_keep")
    response = await _patch(
        auth_client, session_id, {"workspace": "/work/app", "host_id": host_id}
    )
    assert response.status_code == 200, response.text
    stored = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert stored is not None
    assert stored.runner_id == "runner_keep"
    assert stored.git_branch == "feature/old"
    assert working_directory_from_labels(stored.labels) == "/work/app"


async def test_running_session_conflicts(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    """A running session cannot change workspace or host, and the row stays put."""
    hosts = _install_hosts(auth_app, db_uri)
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
    """JSON null is not an unbind; the caller omits the field to leave it."""
    session_id = _seed(
        db_uri, host_id=None, workspace="/work/app", runner_id=None, git_branch=None
    )
    response = await _patch(auth_client, session_id, {"workspace": None, "host_id": None})
    assert response.status_code == 422, response.text


async def test_missing_path_on_online_host_is_unprocessable(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    """An online host that reports the path missing yields 422 and no write."""
    hosts = _install_hosts(auth_app, db_uri)
    host_id = _host(hosts, JENS)
    session_id = _seed(db_uri, host_id=host_id, workspace="/work/app")
    missing = "/missing/project"
    async with _online_host(auth_app.state.host_registry, host_id):
        response = await _patch(auth_client, session_id, {"workspace": missing})
    assert response.status_code == 422, response.text
    assert f"workspace path {missing!r} does not exist on host {host_id!r}" in response.text
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
    assert response.status_code == 422, response.text
    assert "not a directory" in response.text


async def test_offline_host_skips_the_existence_check(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    """A host with no live connection accepts an absolute path that was not stat-ed."""
    hosts = _install_hosts(auth_app, db_uri)
    host_id = _host(hosts, JENS)
    session_id = _seed(db_uri, host_id=None, workspace=None, runner_id=None, git_branch=None)
    windows = "C:/Users/corey/projects"
    response = await _patch(auth_client, session_id, {"workspace": windows, "host_id": host_id})
    assert response.status_code == 200, response.text
    stored = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert stored is not None
    assert stored.workspace == windows
    assert stored.host_id == host_id
    assert working_directory_from_labels(stored.labels) == windows


async def test_host_without_workspace_is_unprocessable(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    """Binding a host onto a session that has no workspace requires one."""
    hosts = _install_hosts(auth_app, db_uri)
    host_id = _host(hosts, JENS)
    session_id = _seed(db_uri, host_id=None, workspace=None, runner_id=None, git_branch=None)
    response = await _patch(auth_client, session_id, {"host_id": host_id})
    assert response.status_code == 422, response.text
    assert "workspace is required" in response.text


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
    # The row is registered, but the socket is not on this replica. An
    # "online" stamp would be read as a wrong-replica miss instead of an
    # offline host that never launched a runner.
    hosts.set_offline(bound_host)
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

    bound = await _patch(auth_client, fork.id, {"workspace": "/src/fork", "host_id": bound_host})
    assert bound.status_code == 200, bound.text
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
    assert host_id in missing
    stored = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert stored is not None
    assert stored.workspace == "/work/app"
    assert stored.host_id == host_id
    assert stored.runner_id == "runner_stale"


async def test_bot_token_rebinds_for_its_owner(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    """An ogc_ account can rebind a session its owner could rebind, and not a foreign host."""
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
