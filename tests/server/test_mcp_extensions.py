"""MCP coverage for the grouped extension tools.

Each write action is dry-run unless ``confirm`` is boolean true. Deletes and
revokes include ``will_lose``. Happy paths hit the real route on the in-process
app. Host filesystem, worktrees, skills, GitHub, environment files, and shell
need a live host or runner; those calls assert the route's own offline or
missing-host error instead of a canned success.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.app import create_app
from omnigent.server.auth import UnifiedAuthProvider
from omnigent.server.mcp_endpoint import tool_definitions
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.comment_store.sqlalchemy_store import SqlAlchemyCommentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.policy_store.sqlalchemy_store import SqlAlchemyPolicyStore
from omnigent.stores.project_store.sqlalchemy_store import SqlAlchemyProjectStore
from tests.server.conftest import _app_client
from tests.server.helpers import create_test_agent
from tests.server.test_mcp_endpoint import _rpc_body, _sync_app, _tool_text

_WRITE_ARGS = {
    "create_session": {"agent_name": "catalog-agent"},
    "send_message": {"text": "stay dry"},
    "interrupt_turn": {},
    "stop_session": {},
    "answer_elicitation": {"elicitation_id": "elicit_dry", "action": "accept"},
    "session_files": {"action": "delete", "file_id": "file_dry"},
    "session_workspace": {"action": "delete", "path": "notes.txt"},
    "session_github": {"action": "attach_pr", "url": "https://github.com/example/repo/pull/1"},
    "manage_session": {"action": "delete"},
    "session_comments": {"action": "delete", "comment_id": "cmt_dry"},
    "host_fs": {"action": "mkdir", "host_id": "host_dry", "path": "/tmp/oa-dry"},
    "project": {"action": "delete", "project_id": "ab" * 16},
    "policy": {"action": "delete", "scope": "session", "policy_id": "pol_dry"},
    "session_mcp": {"action": "delete", "name": "remote"},
    "session_access": {"action": "revoke", "user_id": "alice@example.com"},
    "sharing": {"action": "set", "sharing_mode": "read_only"},
}

_NO_SESSION = {"create_session", "project", "host_fs", "sharing"}
_LOSS = {
    "session_files",
    "session_workspace",
    "manage_session",
    "session_comments",
    "project",
    "policy",
    "session_mcp",
    "session_access",
}


@pytest.fixture()
def mcp_app(
    runtime_init: None, db_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> FastAPI:
    del runtime_init
    monkeypatch.delenv("OMNIGENT_MCP_ENABLED", raising=False)
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    return create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=SqlAlchemyFileStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        artifact_store=artifact_store,
        agent_cache=AgentCache(artifact_store=artifact_store, cache_dir=tmp_path / "cache"),
        comment_store=SqlAlchemyCommentStore(db_uri),
        project_store=SqlAlchemyProjectStore(db_uri),
        policy_store=SqlAlchemyPolicyStore(db_uri),
        host_store=HostStore(db_uri),
        server_config={},
    )


@pytest_asyncio.fixture()
async def mcp_client(mcp_app: FastAPI, mock_llm: Any, tmp_path: Path) -> httpx.AsyncClient:
    async with _app_client(mcp_app, mock_llm, tmp_path) as client:
        yield client


def _write_names() -> set[str]:
    return {
        tool["name"]
        for tool in tool_definitions()
        if "confirm" in tool["inputSchema"]["properties"]
    }


async def _call(
    client: httpx.AsyncClient, name: str, arguments: dict[str, Any] | None = None
) -> httpx.Response:
    return await client.post(
        "/mcp",
        json=_rpc_body("tools/call", {"name": name, "arguments": arguments or {}}),
    )


def _error(response: httpx.Response) -> str:
    result = response.json()["result"]
    assert result.get("isError") is True, result
    return str(result["content"][0]["text"])


def _record_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, Any]]:
    """Record every in-process route call ``/mcp`` makes, then perform it."""
    from omnigent.server.mcp_endpoint import _UNSET, _Api

    seen: list[tuple[str, str, Any]] = []
    original = _Api.call

    async def recording(
        self: _Api,
        method: str,
        path: str,
        body: Any = _UNSET,
        *,
        timeout: float = 30.0,
    ) -> Any:
        seen.append((method, path, None if body is _UNSET else body))
        return await original(self, method, path, body, timeout=timeout)

    monkeypatch.setattr(_Api, "call", recording)
    return seen


def _assert_upstream(
    response: httpx.Response, method: str, path: str, body: Any, seen: list[tuple[str, str, Any]]
) -> None:
    """The tool both called this exact request and reported that request."""
    assert (method, path, body) in seen
    result = response.json()["result"]
    text = str(result["content"][0]["text"])
    if result.get("isError") is True:
        assert f"Omnigent {method} {path} returned HTTP" in text
        return
    parsed = json.loads(text)
    assert isinstance(parsed, dict | list | str)


async def _session(client: httpx.AsyncClient, name: str) -> str:
    agent = await create_test_agent(client, name=name)
    return str(agent["_session_id"])


async def test_every_write_tool_dry_run_ignores_string_confirm(
    mcp_client: httpx.AsyncClient,
) -> None:
    """Every write tool previews unless confirm is boolean true. The string is not enough."""
    assert _write_names() == set(_WRITE_ARGS)
    agent = await create_test_agent(mcp_client, name="catalog-agent")
    session_id = agent["_session_id"]
    before = {
        row["id"] for row in (await mcp_client.get("/v1/sessions?visibility=all")).json()["data"]
    }

    for name, arguments in _WRITE_ARGS.items():
        payload = dict(arguments)
        if name not in _NO_SESSION:
            payload["session_id"] = session_id
        omitted = await _call(mcp_client, name, payload)
        planned = _tool_text(omitted)
        assert planned["dry_run"] is True, name
        assert planned["method"]
        assert planned["path"]
        assert isinstance(planned["body"], dict)
        if name in _LOSS:
            assert isinstance(planned["will_lose"], str) and planned["will_lose"], name
        else:
            assert "will_lose" not in planned, name

        quoted = dict(payload)
        quoted["confirm"] = "true"
        still_dry = await _call(mcp_client, name, quoted)
        assert _tool_text(still_dry)["dry_run"] is True, name

        numbered = dict(payload)
        numbered["confirm"] = 1
        still_number = await _call(mcp_client, name, numbered)
        assert _tool_text(still_number)["dry_run"] is True, name

    after = {
        row["id"] for row in (await mcp_client.get("/v1/sessions?visibility=all")).json()["data"]
    }
    assert after == before


async def test_elicitation_answer_and_wait(mcp_client: httpx.AsyncClient) -> None:
    """A pending elicitation is readable. confirm delivers the verdict; wait resumes the turn."""
    from omnigent.runtime import pending_elicitations, session_stream
    from omnigent.server._elicitation_registry import (
        _harness_elicitation_owners,
        _harness_elicitation_registry,
    )

    session_id = await _session(mcp_client, "elicit-ext")
    elicitation_id = "elicit_mcp_ext"
    pending_elicitations.record_publish(
        session_id,
        {
            "type": "response.elicitation_request",
            "elicitation_id": elicitation_id,
            "params": {"message": "Allow the tool?", "phase": "tool"},
        },
    )
    pending = _tool_text(
        await _call(
            mcp_client,
            "get_elicitation",
            {"session_id": session_id, "elicitation_id": elicitation_id},
        )
    )
    assert pending["status"] == "pending"
    assert pending["message"] == "Allow the tool?"

    future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    _harness_elicitation_registry[elicitation_id] = future
    _harness_elicitation_owners[elicitation_id] = session_id
    try:
        dry = _tool_text(
            await _call(
                mcp_client,
                "answer_elicitation",
                {
                    "session_id": session_id,
                    "elicitation_id": elicitation_id,
                    "action": "deny",
                    "wait": True,
                    "timeout_seconds": 5,
                },
            )
        )
        assert dry["dry_run"] is True
        assert dry["body"]["action"] == "decline"
        assert "timeout_seconds" in dry["wait"]
        assert future.done() is False

        resolved = _tool_text(
            await _call(
                mcp_client,
                "answer_elicitation",
                {
                    "session_id": session_id,
                    "elicitation_id": elicitation_id,
                    "action": "accept",
                    "confirm": True,
                },
            )
        )
        assert resolved == {"queued": False}
        assert future.result().action == "accept"
        cleared = _tool_text(
            await _call(
                mcp_client,
                "get_elicitation",
                {"session_id": session_id, "elicitation_id": elicitation_id},
            )
        )
        assert cleared["status"] == "resolved"
    finally:
        _harness_elicitation_registry.pop(elicitation_id, None)
        _harness_elicitation_owners.pop(elicitation_id, None)

    waited_id = "elicit_mcp_wait"
    waited: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    _harness_elicitation_registry[waited_id] = waited
    _harness_elicitation_owners[waited_id] = session_id

    async def finish_when_resolved() -> None:
        verdict = await asyncio.wait_for(waited, timeout=5)
        assert verdict.action == "accept"
        assert verdict.content["text"] == "yes"
        session_stream.publish(
            session_id, {"type": "response.output_text.delta", "delta": "approved"}
        )
        session_stream.publish(session_id, {"type": "session.status", "status": "idle"})

    publisher = asyncio.create_task(finish_when_resolved())
    try:
        answered = _tool_text(
            await _call(
                mcp_client,
                "answer_elicitation",
                {
                    "session_id": session_id,
                    "elicitation_id": waited_id,
                    "action": "approve",
                    "text": "yes",
                    "wait": True,
                    "timeout_seconds": 10,
                    "confirm": True,
                },
            )
        )
        await publisher
    finally:
        publisher.cancel()
        _harness_elicitation_registry.pop(waited_id, None)
        _harness_elicitation_owners.pop(waited_id, None)
    assert answered["resolved"] == {"queued": False}
    assert answered["reply"] == "approved"
    assert answered["end_reason"] == "idle"
    assert answered["timed_out"] is False


async def test_session_files_and_workspace(
    mcp_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """List and read a real uploaded file. Environment writes hit the runner-less route."""
    session_id = await _session(mcp_client, "files-ext")
    empty = _tool_text(
        await _call(mcp_client, "session_files", {"session_id": session_id, "action": "list"})
    )
    assert empty["data"] == []
    uploaded = await mcp_client.post(
        f"/v1/sessions/{session_id}/resources/files",
        files={"file": ("note.txt", b"hello from mcp", "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    file_id = uploaded.json()["id"]

    listed = _tool_text(
        await _call(mcp_client, "session_files", {"session_id": session_id, "action": "list"})
    )
    assert any(row["id"] == file_id for row in listed["data"])
    meta = _tool_text(
        await _call(
            mcp_client,
            "session_files",
            {"session_id": session_id, "action": "get", "file_id": file_id},
        )
    )
    assert meta["id"] == file_id
    body = _tool_text(
        await _call(
            mcp_client,
            "session_files",
            {"session_id": session_id, "action": "content", "file_id": file_id},
        )
    )
    assert body["content"] == "hello from mcp"

    dry = _tool_text(
        await _call(
            mcp_client,
            "session_files",
            {"session_id": session_id, "action": "delete", "file_id": file_id},
        )
    )
    assert dry["dry_run"] is True
    assert file_id in dry["will_lose"]
    still = _tool_text(
        await _call(mcp_client, "session_files", {"session_id": session_id, "action": "list"})
    )
    assert any(row["id"] == file_id for row in still["data"])
    removed = _tool_text(
        await _call(
            mcp_client,
            "session_files",
            {"session_id": session_id, "action": "delete", "file_id": file_id, "confirm": True},
        )
    )
    assert removed["deleted"] is True

    workspace_dry = _tool_text(
        await _call(
            mcp_client,
            "session_workspace",
            {"session_id": session_id, "action": "shell", "command": "pwd"},
        )
    )
    assert workspace_dry["dry_run"] is True
    assert "approval gate" in workspace_dry["effect"]
    seen = _record_calls(monkeypatch)
    root = f"/v1/sessions/{session_id}/resources/environments/default"
    expected = (
        ("list", {}, "GET", f"{root}/filesystem", None),
        ("read", {"path": "src/app.py"}, "GET", f"{root}/filesystem/src/app.py", None),
        ("search", {"q": "app"}, "GET", f"{root}/search?q=app", None),
        ("changes", {}, "GET", f"{root}/changes", None),
        ("diff", {"path": "src/app.py"}, "GET", f"{root}/diff/src/app.py", None),
        (
            "write",
            {"path": "notes.txt", "content": "hello", "confirm": True},
            "PUT",
            f"{root}/filesystem/notes.txt",
            {"content": "hello"},
        ),
        (
            "shell",
            {"command": "pwd", "confirm": True},
            "POST",
            f"{root}/shell",
            {"command": "pwd"},
        ),
    )
    for action, arguments, method, path, body in expected:
        response = await _call(
            mcp_client,
            "session_workspace",
            {"session_id": session_id, "action": action, **arguments},
        )
        _assert_upstream(response, method, path, body, seen)


async def test_session_github_state(
    mcp_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitHub reads and a confirmed attach hit the route and surface its runner error."""
    session_id = await _session(mcp_client, "github-ext")
    seen = _record_calls(monkeypatch)
    root = f"/v1/sessions/{session_id}/resources/github"
    for action, arguments, path in (
        ("info", {}, root),
        ("changes", {}, f"{root}/changes"),
        ("diff", {}, f"{root}/diff"),
        ("diff", {"path": "src/app.py"}, f"{root}/diff/src/app.py"),
    ):
        response = await _call(
            mcp_client,
            "session_github",
            {"session_id": session_id, "action": action, **arguments},
        )
        _assert_upstream(response, "GET", path, None, seen)

    dry = _tool_text(
        await _call(
            mcp_client,
            "session_github",
            {
                "session_id": session_id,
                "action": "preferences",
                "account": "octocat",
            },
        )
    )
    assert dry["dry_run"] is True
    assert dry["path"].endswith("/resources/github/preferences")
    refused = await _call(
        mcp_client,
        "session_github",
        {
            "session_id": session_id,
            "action": "attach_pr",
            "url": "https://github.com/example/repo/pull/2",
            "confirm": True,
        },
    )
    _assert_upstream(
        refused,
        "POST",
        f"{root}/prs",
        {"url": "https://github.com/example/repo/pull/2", "action": "attach"},
        seen,
    )


async def test_manage_session(mcp_client: httpx.AsyncClient) -> None:
    """Rename, archive, label, fork, and reset hit the routes. Dry-run delete keeps the session."""
    session_id = await _session(mcp_client, "manage-ext")
    renamed = _tool_text(
        await _call(
            mcp_client,
            "manage_session",
            {
                "session_id": session_id,
                "action": "update",
                "title": "renamed by mcp",
                "confirm": True,
            },
        )
    )
    assert renamed["title"] == "renamed by mcp"
    archived = _tool_text(
        await _call(
            mcp_client,
            "manage_session",
            {"session_id": session_id, "action": "update", "archived": True, "confirm": True},
        )
    )
    assert archived["archived"] is True
    labeled = _tool_text(
        await _call(
            mcp_client,
            "manage_session",
            {
                "session_id": session_id,
                "action": "update",
                "labels": {"mcp": "yes"},
                "archived": False,
                "confirm": True,
            },
        )
    )
    assert labeled["archived"] is False
    labels = _tool_text(
        await _call(mcp_client, "manage_session", {"session_id": session_id, "action": "labels"})
    )
    assert labels["labels"]["mcp"] == "yes"
    children = _tool_text(
        await _call(mcp_client, "manage_session", {"session_id": session_id, "action": "children"})
    )
    assert children["data"] == []
    agent = _tool_text(
        await _call(mcp_client, "manage_session", {"session_id": session_id, "action": "agent"})
    )
    assert agent["name"]

    dry_fork = _tool_text(
        await _call(
            mcp_client,
            "manage_session",
            {"session_id": session_id, "action": "fork", "title": "forked"},
        )
    )
    assert dry_fork["dry_run"] is True
    forked = _tool_text(
        await _call(
            mcp_client,
            "manage_session",
            {
                "session_id": session_id,
                "action": "fork",
                "title": "forked",
                "confirm": True,
            },
        )
    )
    assert forked["id"] != session_id
    assert forked["title"] == "forked"

    reset_dry = _tool_text(
        await _call(
            mcp_client,
            "manage_session",
            {
                "session_id": session_id,
                "action": "reset_model",
                "expected_model_override": "claude-opus-4-7",
            },
        )
    )
    assert "still current" in reset_dry["effect"]
    reset = _tool_text(
        await _call(
            mcp_client,
            "manage_session",
            {
                "session_id": session_id,
                "action": "reset_model",
                "expected_model_override": "claude-opus-4-7",
                "confirm": True,
            },
        )
    )
    assert reset["reset"] is False

    dry = _tool_text(
        await _call(mcp_client, "manage_session", {"session_id": session_id, "action": "delete"})
    )
    assert dry["dry_run"] is True
    assert session_id in dry["will_lose"]
    assert "conversation" in dry["will_lose"]
    assert (
        _tool_text(await _call(mcp_client, "get_session", {"session_id": session_id}))["id"]
        == session_id
    )
    deleted = _tool_text(
        await _call(
            mcp_client,
            "manage_session",
            {"session_id": session_id, "action": "delete", "confirm": True},
        )
    )
    assert deleted.get("deleted") is True or deleted.get("id") == session_id
    missing = await _call(mcp_client, "get_session", {"session_id": session_id})
    assert missing.json()["result"]["isError"] is True


async def test_comments_round_trip(mcp_client: httpx.AsyncClient) -> None:
    """Add, list, send, and delete a comment. send does not post a turn."""
    session_id = await _session(mcp_client, "comments-ext")
    before = _tool_text(await _call(mcp_client, "get_session_items", {"session_id": session_id}))
    created = _tool_text(
        await _call(
            mcp_client,
            "session_comments",
            {
                "session_id": session_id,
                "action": "add",
                "path": "src/app.py",
                "body": "check this",
                "start_index": 0,
                "end_index": 4,
                "anchor_content": "check",
                "confirm": True,
            },
        )
    )
    comment_id = created["id"]
    listed = _tool_text(
        await _call(mcp_client, "session_comments", {"session_id": session_id, "action": "list"})
    )
    assert any(row["id"] == comment_id and row["body"] == "check this" for row in listed)

    dry_send = _tool_text(
        await _call(
            mcp_client,
            "session_comments",
            {"session_id": session_id, "action": "send", "comment_ids": [comment_id]},
        )
    )
    assert dry_send["dry_run"] is True
    assert "does not post" in dry_send["effect"].lower() or "Does not post" in dry_send["effect"]
    still_open = _tool_text(
        await _call(mcp_client, "session_comments", {"session_id": session_id, "action": "list"})
    )
    assert any(row["id"] == comment_id and row["status"] != "addressed" for row in still_open)

    sent = _tool_text(
        await _call(
            mcp_client,
            "session_comments",
            {
                "session_id": session_id,
                "action": "send",
                "comment_ids": [comment_id],
                "confirm": True,
            },
        )
    )
    assert comment_id in sent["sent_comment_ids"]
    assert "check this" in sent["formatted_message"]
    addressed = _tool_text(
        await _call(mcp_client, "session_comments", {"session_id": session_id, "action": "list"})
    )
    assert any(row["id"] == comment_id and row["status"] == "addressed" for row in addressed)
    after = _tool_text(await _call(mcp_client, "get_session_items", {"session_id": session_id}))
    assert after.get("data", after) == before.get("data", before)

    edited = _tool_text(
        await _call(
            mcp_client,
            "session_comments",
            {
                "session_id": session_id,
                "action": "update",
                "comment_id": comment_id,
                "body": "check that",
                "confirm": True,
            },
        )
    )
    assert edited["body"] == "check that"

    dry = _tool_text(
        await _call(
            mcp_client,
            "session_comments",
            {"session_id": session_id, "action": "delete", "comment_id": comment_id},
        )
    )
    assert comment_id in dry["will_lose"]
    removed = _tool_text(
        await _call(
            mcp_client,
            "session_comments",
            {
                "session_id": session_id,
                "action": "delete",
                "comment_id": comment_id,
                "confirm": True,
            },
        )
    )
    assert removed["deleted"] is True
    empty = _tool_text(
        await _call(mcp_client, "session_comments", {"session_id": session_id, "action": "list"})
    )
    assert empty == []


async def test_host_filesystem_and_worktrees_report_a_missing_host(
    mcp_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No live host in this app. Dry-run skips the POST; confirm returns the route 404."""
    host_id = "ab" * 16
    seen = _record_calls(monkeypatch)
    dry = _tool_text(
        await _call(
            mcp_client,
            "host_fs",
            {"action": "mkdir", "host_id": host_id, "path": "/tmp/oa-mcp-dir"},
        )
    )
    assert dry["dry_run"] is True
    assert dry["method"] == "POST"
    root = f"/v1/hosts/{host_id}"
    refused = await _call(
        mcp_client,
        "host_fs",
        {"action": "mkdir", "host_id": host_id, "path": "/tmp/oa-mcp-dir", "confirm": True},
    )
    _assert_upstream(refused, "POST", f"{root}/directories", {"path": "/tmp/oa-mcp-dir"}, seen)
    assert "host not found" in _error(refused)
    listing = await _call(mcp_client, "host_fs", {"action": "list", "host_id": host_id})
    _assert_upstream(listing, "GET", f"{root}/filesystem", None, seen)
    nested = await _call(
        mcp_client, "host_fs", {"action": "list", "host_id": host_id, "path": "src"}
    )
    _assert_upstream(nested, "GET", f"{root}/filesystem/src", None, seen)
    worktrees = await _call(
        mcp_client,
        "host_fs",
        {"action": "worktrees", "host_id": host_id, "path": "/tmp/oa-mcp-repo"},
    )
    _assert_upstream(worktrees, "GET", f"{root}/worktrees?path=%2Ftmp%2Foa-mcp-repo", None, seen)


async def test_projects_usage_and_order(mcp_client: httpx.AsyncClient) -> None:
    """Create, read, rename, reorder, and delete a project. Usage stays readable."""
    usage = _tool_text(await _call(mcp_client, "project", {"action": "usage"}))
    assert usage["object"] == "usage_report"

    dry = _tool_text(await _call(mcp_client, "project", {"action": "create", "name": "Gamma"}))
    assert dry["dry_run"] is True
    created = _tool_text(
        await _call(
            mcp_client,
            "project",
            {
                "action": "create",
                "name": "Gamma",
                "config": {"workspace": "/work/gamma"},
                "confirm": True,
            },
        )
    )
    project_id = created["id"]
    other = _tool_text(
        await _call(mcp_client, "project", {"action": "create", "name": "Delta", "confirm": True})
    )
    fetched = _tool_text(
        await _call(mcp_client, "project", {"action": "get", "project_id": project_id})
    )
    assert fetched["name"] == "Gamma"
    assert fetched["config"]["workspace"] == "/work/gamma"

    renamed = _tool_text(
        await _call(
            mcp_client,
            "project",
            {"action": "update", "project_id": project_id, "name": "Gamma 2", "confirm": True},
        )
    )
    assert renamed["name"] == "Gamma 2"
    order_before = _tool_text(await _call(mcp_client, "project", {"action": "order"}))
    assert "ordered_project_ids" in order_before or "sort_mode" in order_before
    ordered = _tool_text(
        await _call(
            mcp_client,
            "project",
            {
                "action": "order",
                "ordered_project_ids": [other["id"], project_id],
                "confirm": True,
            },
        )
    )
    assert ordered["ordered_project_ids"][:2] == [other["id"], project_id]

    kept = _tool_text(
        await _call(mcp_client, "project", {"action": "delete", "project_id": project_id})
    )
    assert kept["dry_run"] is True
    assert "unfiled" in kept["will_lose"]
    assert (
        _tool_text(
            await _call(mcp_client, "project", {"action": "get", "project_id": project_id})
        )["name"]
        == "Gamma 2"
    )
    removed = _tool_text(
        await _call(
            mcp_client,
            "project",
            {"action": "delete", "project_id": project_id, "confirm": True},
        )
    )
    assert removed["deleted"] is True
    missing = await _call(mcp_client, "project", {"action": "get", "project_id": project_id})
    assert "Project not found" in _error(missing)


async def test_policies_global_and_session(mcp_client: httpx.AsyncClient) -> None:
    """Session and global policy writes hit the store. Evaluate is per session."""
    from omnigent.policies.registry import load_registry

    # The in-process client does not enter the server lifespan, which is what
    # normally fills the registry. Load it the same way startup does.
    load_registry()
    session_id = await _session(mcp_client, "policy-ext")
    registry = _tool_text(await _call(mcp_client, "policy", {"action": "registry"}))
    assert registry["object"] == "list"
    assert registry["data"]
    handler = next(
        row["handler"]
        for row in registry["data"]
        if row.get("handler") == "omnigent.policies.builtins.safety.max_tool_calls_per_session"
    )

    created = _tool_text(
        await _call(
            mcp_client,
            "policy",
            {
                "action": "create",
                "scope": "session",
                "session_id": session_id,
                "name": "mcp-gate",
                "type": "url",
                "handler": "https://example.com/policies/eval",
                "confirm": True,
            },
        )
    )
    policy_id = created["id"]
    one = _tool_text(
        await _call(
            mcp_client,
            "policy",
            {
                "action": "get",
                "scope": "session",
                "session_id": session_id,
                "policy_id": policy_id,
            },
        )
    )
    assert one["id"] == policy_id
    updated = _tool_text(
        await _call(
            mcp_client,
            "policy",
            {
                "action": "update",
                "scope": "session",
                "session_id": session_id,
                "policy_id": policy_id,
                "enabled": False,
                "confirm": True,
            },
        )
    )
    assert updated["enabled"] is False

    dry_eval = _tool_text(
        await _call(
            mcp_client,
            "policy",
            {
                "action": "evaluate",
                "session_id": session_id,
                "event": {"type": "PHASE_TOOL_CALL", "data": {"name": "read_file"}},
            },
        )
    )
    assert dry_eval["dry_run"] is True
    assert "rewrite" in dry_eval["effect"]
    verdict = _tool_text(
        await _call(
            mcp_client,
            "policy",
            {
                "action": "evaluate",
                "session_id": session_id,
                "event": {"type": "PHASE_TOOL_CALL", "data": {"name": "read_file"}},
                "confirm": True,
            },
        )
    )
    assert verdict.get("result") or verdict.get("action") or "result" in verdict

    dry = _tool_text(
        await _call(
            mcp_client,
            "policy",
            {
                "action": "delete",
                "scope": "session",
                "session_id": session_id,
                "policy_id": policy_id,
            },
        )
    )
    assert policy_id in dry["will_lose"]
    removed = _tool_text(
        await _call(
            mcp_client,
            "policy",
            {
                "action": "delete",
                "scope": "session",
                "session_id": session_id,
                "policy_id": policy_id,
                "confirm": True,
            },
        )
    )
    assert removed.get("deleted") is True

    global_dry = _tool_text(
        await _call(
            mcp_client,
            "policy",
            {
                "action": "create",
                "scope": "global",
                "name": "mcp-global",
                "type": "url",
                "handler": "https://example.com/policies/eval",
            },
        )
    )
    assert "every session" in global_dry["effect"]
    global_created = _tool_text(
        await _call(
            mcp_client,
            "policy",
            {
                "action": "create",
                "scope": "global",
                "name": "mcp-global",
                "type": "python",
                "handler": handler,
                "factory_params": {"limit": 3},
                "confirm": True,
            },
        )
    )
    global_id = global_created["id"]
    listed = _tool_text(await _call(mcp_client, "policy", {"action": "list", "scope": "global"}))
    assert any(row["id"] == global_id for row in listed["data"])
    gone = _tool_text(
        await _call(
            mcp_client,
            "policy",
            {"action": "delete", "scope": "global", "policy_id": global_id, "confirm": True},
        )
    )
    assert gone.get("deleted") is True


async def test_session_mcp_server_round_trip(mcp_client: httpx.AsyncClient) -> None:
    """Declaring an HTTP MCP server mutates the session agent. Dry-run delete does not."""
    session_id = await _session(mcp_client, "mcp-servers-ext")
    created = _tool_text(
        await _call(
            mcp_client,
            "session_mcp",
            {
                "session_id": session_id,
                "action": "create",
                "name": "remote",
                "transport": "http",
                "url": "https://mcp.example.com/mcp",
                "confirm": True,
            },
        )
    )
    assert created["name"] == "remote"
    updated = _tool_text(
        await _call(
            mcp_client,
            "session_mcp",
            {
                "session_id": session_id,
                "action": "update",
                "name": "remote",
                "transport": "http",
                "url": "https://mcp.example.com/v2",
                "confirm": True,
            },
        )
    )
    assert updated["url"] == "https://mcp.example.com/v2" or updated["name"] == "remote"
    listed = _tool_text(
        await _call(mcp_client, "session_mcp", {"session_id": session_id, "action": "list"})
    )
    assert any(row["name"] == "remote" for row in listed["data"])

    dry = _tool_text(
        await _call(
            mcp_client,
            "session_mcp",
            {"session_id": session_id, "action": "delete", "name": "remote"},
        )
    )
    assert "remote" in dry["will_lose"]
    still = _tool_text(
        await _call(mcp_client, "session_mcp", {"session_id": session_id, "action": "list"})
    )
    assert any(row["name"] == "remote" for row in still["data"])
    removed = _tool_text(
        await _call(
            mcp_client,
            "session_mcp",
            {"session_id": session_id, "action": "delete", "name": "remote", "confirm": True},
        )
    )
    assert removed == {"ok": True}


async def test_harnesses_skills_and_extensions(
    mcp_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Harnesses and extensions list. Skills on a session with no host return the route 409."""
    session_id = await _session(mcp_client, "skills-ext")
    harnesses = _tool_text(await _call(mcp_client, "list_harnesses"))
    assert isinstance(harnesses, dict)
    extensions = _tool_text(await _call(mcp_client, "list_extensions"))
    assert isinstance(extensions["data"], list)
    if extensions["data"]:
        extension_id = extensions["data"][0]["id"]
        one = _tool_text(await _call(mcp_client, "get_extension", {"extension_id": extension_id}))
        assert one["id"] == extension_id
    else:
        missing = await _call(mcp_client, "get_extension", {"extension_id": "not-installed"})
        assert missing.json()["result"]["isError"] is True

    seen = _record_calls(monkeypatch)
    skills = await _call(mcp_client, "list_skills", {"session_id": session_id})
    _assert_upstream(skills, "GET", f"/v1/skills?session_id={session_id}", None, seen)
    assert "session host is not ready" in _error(skills)


async def test_sharing_dry_run_and_confirm(
    mcp_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dry-run leaves sharing alone. confirm writes the override, or the route refuses it."""
    monkeypatch.setenv("OMNIGENT_ADMIN_CREDENTIALS_PATH", str(tmp_path / "admin-creds"))
    before = _tool_text(await _call(mcp_client, "sharing", {"action": "get"}))
    assert before["object"] == "sharing"
    target = "read_only" if before["sharing_mode"] != "read_only" else "on"
    dry = _tool_text(await _call(mcp_client, "sharing", {"action": "set", "sharing_mode": target}))
    assert dry["dry_run"] is True
    assert dry["method"] == "PUT"
    assert (
        _tool_text(await _call(mcp_client, "sharing", {"action": "get"}))["sharing_mode"]
        == before["sharing_mode"]
    )

    response = await _call(
        mcp_client, "sharing", {"action": "set", "sharing_mode": target, "confirm": True}
    )
    result = response.json()["result"]
    if result.get("isError") is True:
        assert "managed" in result["content"][0]["text"]
        assert (
            _tool_text(await _call(mcp_client, "sharing", {"action": "get"}))["sharing_mode"]
            == before["sharing_mode"]
        )
        return
    changed = json.loads(result["content"][0]["text"])
    assert changed["sharing_mode"] == target
    restored = _tool_text(
        await _call(
            mcp_client,
            "sharing",
            {"action": "set", "sharing_mode": before["sharing_mode"], "confirm": True},
        )
    )
    assert restored["sharing_mode"] == before["sharing_mode"]


async def test_permissions_grant_and_revoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Local single-user can grant and revoke. A string confirm does not grant."""
    monkeypatch.setenv("OMNIGENT_AUTH_PROVIDER", "header")
    provider = UnifiedAuthProvider(
        source="header", local_single_user=True, header_name="X-Forwarded-Email"
    )
    app = _sync_app(tmp_path, monkeypatch, auth_provider=provider, server_config={})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        session_id = await _session(client, "perm-ext")
        dry = _tool_text(
            await _call(
                client,
                "session_access",
                {
                    "session_id": session_id,
                    "action": "grant",
                    "user_id": "alice@example.com",
                    "level": 1,
                    "confirm": "true",
                },
            )
        )
        assert dry["dry_run"] is True
        granted = _tool_text(
            await _call(
                client,
                "session_access",
                {
                    "session_id": session_id,
                    "action": "grant",
                    "user_id": "alice@example.com",
                    "level": 1,
                    "confirm": True,
                },
            )
        )
        assert granted["user_id"] == "alice@example.com"
        assert granted["level"] == 1
        listed = _tool_text(
            await _call(client, "session_access", {"session_id": session_id, "action": "list"})
        )
        assert any(row["user_id"] == "alice@example.com" for row in listed["permissions"])
        owner = _tool_text(
            await _call(client, "session_access", {"session_id": session_id, "action": "owner"})
        )
        assert owner["owner"] == "local"

        kept = _tool_text(
            await _call(
                client,
                "session_access",
                {
                    "session_id": session_id,
                    "action": "revoke",
                    "user_id": "alice@example.com",
                },
            )
        )
        assert kept["dry_run"] is True
        assert "alice@example.com" in kept["will_lose"]
        revoked = _tool_text(
            await _call(
                client,
                "session_access",
                {
                    "session_id": session_id,
                    "action": "revoke",
                    "user_id": "alice@example.com",
                    "confirm": True,
                },
            )
        )
        assert revoked == {"ok": True}
        after = _tool_text(
            await _call(client, "session_access", {"session_id": session_id, "action": "list"})
        )
        assert all(row["user_id"] != "alice@example.com" for row in after["permissions"])


async def test_path_traversal_is_rejected_before_the_request(
    mcp_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``..`` is a tool error. confirm true does not send the collapsed URL."""
    session_id = await _session(mcp_client, "traversal-ext")
    before = {
        row["id"] for row in (await mcp_client.get("/v1/sessions?visibility=all")).json()["data"]
    }
    seen = _record_calls(monkeypatch)
    attacks = (
        (
            "session_workspace",
            {"session_id": session_id, "action": "delete", "path": "../../../..", "confirm": True},
        ),
        (
            "session_workspace",
            {
                "session_id": session_id,
                "action": "delete",
                "path": "foo/../../etc/passwd",
                "confirm": True,
            },
        ),
        (
            "session_workspace",
            {
                "session_id": session_id,
                "action": "delete",
                "path": "%2e%2e/%2e%2e",
                "confirm": True,
            },
        ),
        (
            "session_workspace",
            {"session_id": "..", "action": "delete", "path": "notes.txt", "confirm": True},
        ),
        (
            "manage_session",
            {"session_id": "..", "action": "delete", "confirm": True},
        ),
        (
            "session_files",
            {"session_id": session_id, "action": "delete", "file_id": "..", "confirm": True},
        ),
        (
            "host_fs",
            {"action": "list", "host_id": "ab" * 16, "path": "../.."},
        ),
    )
    for name, arguments in attacks:
        assert "invalid" in _error(await _call(mcp_client, name, arguments))
    assert seen == []
    preview = _tool_text(
        await _call(
            mcp_client,
            "session_workspace",
            {"session_id": session_id, "action": "delete", "path": "my notes.txt"},
        )
    )
    encoded = f"/v1/sessions/{session_id}/resources/environments/default/filesystem/my%20notes.txt"
    assert preview["dry_run"] is True
    assert preview["path"] == encoded
    assert preview["method"] == "DELETE"
    after = {
        row["id"] for row in (await mcp_client.get("/v1/sessions?visibility=all")).json()["data"]
    }
    assert after == before
    assert (await mcp_client.get(f"/v1/sessions/{session_id}")).status_code == 200
