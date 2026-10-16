"""MCP coverage for the extension tools.

Each write is dry-run unless ``confirm`` is boolean true. Happy paths hit the
real route on the in-process app. Host filesystem, worktrees, skills, GitHub,
and environment diffs need a live host or runner; those calls assert the
route's own offline or missing-host error instead of a canned success.
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
    "resolve_elicitation": {"elicitation_id": "elicit_dry", "action": "accept"},
    "update_session": {"title": "dry title"},
    "delete_session": {},
    "add_comment": {"path": "a.py", "body": "note", "start_index": 0, "end_index": 1},
    "update_comment": {"comment_id": "cmt_dry", "body": "edited"},
    "delete_comment": {"comment_id": "cmt_dry"},
    "create_host_directory": {"host_id": "host_dry", "path": "/tmp/oa-dry"},
    "create_project": {"name": "Dry catalog"},
    "update_project": {"project_id": "proj_dry", "name": "Renamed"},
    "delete_project": {"project_id": "proj_dry"},
    "create_session_policy": {
        "name": "gate",
        "type": "url",
        "handler": "https://example.com/policies/eval",
    },
    "delete_session_policy": {"policy_id": "pol_dry"},
    "add_session_mcp_server": {
        "name": "remote",
        "transport": "http",
        "url": "https://mcp.example.com/mcp",
    },
    "delete_session_mcp_server": {"name": "remote"},
    "grant_session_access": {"user_id": "alice@example.com", "level": 1},
    "revoke_session_access": {"user_id": "alice@example.com"},
    "set_sharing": {"sharing_mode": "read_only"},
    "attach_session_pull_request": {"url": "https://github.com/example/repo/pull/1"},
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
        if name != "create_session" and name not in {
            "create_project",
            "update_project",
            "delete_project",
            "create_host_directory",
            "set_sharing",
        }:
            payload["session_id"] = session_id
        omitted = await _call(mcp_client, name, payload)
        planned = _tool_text(omitted)
        assert planned["dry_run"] is True, name
        assert planned["method"]
        assert planned["path"]
        assert isinstance(planned["body"], dict)

        quoted = dict(payload)
        quoted["confirm"] = "true"
        still_dry = await _call(mcp_client, name, quoted)
        assert _tool_text(still_dry)["dry_run"] is True, name

    after = {
        row["id"] for row in (await mcp_client.get("/v1/sessions?visibility=all")).json()["data"]
    }
    assert after == before


async def test_elicitation_answer_and_read(mcp_client: httpx.AsyncClient) -> None:
    """A pending elicitation is readable, and confirm delivers the verdict to the parked future."""
    from omnigent.runtime import pending_elicitations
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
                "resolve_elicitation",
                {"session_id": session_id, "elicitation_id": elicitation_id, "action": "decline"},
            )
        )
        assert dry["dry_run"] is True
        assert future.done() is False

        resolved = _tool_text(
            await _call(
                mcp_client,
                "resolve_elicitation",
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


async def test_session_files_diffs_and_github_state(mcp_client: httpx.AsyncClient) -> None:
    """File list is empty. Diff and GitHub reads hit the route and surface its runner error."""
    session_id = await _session(mcp_client, "files-ext")
    files = _tool_text(await _call(mcp_client, "list_session_files", {"session_id": session_id}))
    assert files["data"] == []

    for name, arguments in (
        (
            "list_environment_changes",
            {"session_id": session_id, "environment_id": "env_missing"},
        ),
        (
            "get_environment_diff",
            {"session_id": session_id, "environment_id": "env_missing", "path": "src/app.py"},
        ),
        ("get_session_github", {"session_id": session_id}),
        ("list_session_github_changes", {"session_id": session_id}),
    ):
        response = await _call(mcp_client, name, arguments)
        text = response.json()["result"]["content"][0]["text"]
        if response.json()["result"].get("isError") is True:
            assert session_id in text
        else:
            assert json.loads(text)

    dry = _tool_text(
        await _call(
            mcp_client,
            "attach_session_pull_request",
            {"session_id": session_id, "url": "https://github.com/example/repo/pull/2"},
        )
    )
    assert dry["dry_run"] is True
    assert dry["path"].endswith("/resources/github/prs")
    refused = await _call(
        mcp_client,
        "attach_session_pull_request",
        {
            "session_id": session_id,
            "url": "https://github.com/example/repo/pull/2",
            "confirm": True,
        },
    )
    assert session_id in _error(refused)


async def test_session_update_and_delete(mcp_client: httpx.AsyncClient) -> None:
    """Title patch sticks. Dry-run delete keeps the session; confirm removes it."""
    session_id = await _session(mcp_client, "manage-ext")
    renamed = _tool_text(
        await _call(
            mcp_client,
            "update_session",
            {"session_id": session_id, "title": "renamed by mcp", "confirm": True},
        )
    )
    assert renamed["title"] == "renamed by mcp"
    fetched = _tool_text(await _call(mcp_client, "get_session", {"session_id": session_id}))
    assert fetched["title"] == "renamed by mcp"

    dry = _tool_text(await _call(mcp_client, "delete_session", {"session_id": session_id}))
    assert dry["dry_run"] is True
    assert dry["method"] == "DELETE"
    assert (
        _tool_text(await _call(mcp_client, "get_session", {"session_id": session_id}))["id"]
        == session_id
    )

    deleted = _tool_text(
        await _call(mcp_client, "delete_session", {"session_id": session_id, "confirm": True})
    )
    assert deleted.get("deleted") is True or deleted.get("id") == session_id
    missing = await _call(mcp_client, "get_session", {"session_id": session_id})
    assert missing.json()["result"]["isError"] is True


async def test_comments_round_trip(mcp_client: httpx.AsyncClient) -> None:
    """Add, list, edit, and delete a comment. Dry-run delete leaves it in place."""
    session_id = await _session(mcp_client, "comments-ext")
    created = _tool_text(
        await _call(
            mcp_client,
            "add_comment",
            {
                "session_id": session_id,
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
    listed = _tool_text(await _call(mcp_client, "list_comments", {"session_id": session_id}))
    assert any(row["id"] == comment_id and row["body"] == "check this" for row in listed)

    edited = _tool_text(
        await _call(
            mcp_client,
            "update_comment",
            {
                "session_id": session_id,
                "comment_id": comment_id,
                "body": "check that",
                "status": "addressed",
                "confirm": True,
            },
        )
    )
    assert edited["body"] == "check that"
    assert edited["status"] == "addressed"

    dry = _tool_text(
        await _call(
            mcp_client,
            "delete_comment",
            {"session_id": session_id, "comment_id": comment_id},
        )
    )
    assert dry["dry_run"] is True
    still = _tool_text(await _call(mcp_client, "list_comments", {"session_id": session_id}))
    assert any(row["id"] == comment_id for row in still)

    removed = _tool_text(
        await _call(
            mcp_client,
            "delete_comment",
            {"session_id": session_id, "comment_id": comment_id, "confirm": True},
        )
    )
    assert removed["deleted"] is True
    empty = _tool_text(await _call(mcp_client, "list_comments", {"session_id": session_id}))
    assert empty == []


async def test_host_filesystem_and_worktrees_report_a_missing_host(
    mcp_client: httpx.AsyncClient,
) -> None:
    """No live host in this app. Dry-run skips the POST; confirm returns the route 404."""
    host_id = "ab" * 16
    dry = _tool_text(
        await _call(
            mcp_client,
            "create_host_directory",
            {"host_id": host_id, "path": "/tmp/oa-mcp-dir"},
        )
    )
    assert dry["dry_run"] is True
    assert dry["method"] == "POST"
    refused = await _call(
        mcp_client,
        "create_host_directory",
        {"host_id": host_id, "path": "/tmp/oa-mcp-dir", "confirm": True},
    )
    assert "host not found" in _error(refused)
    listing = await _call(mcp_client, "list_host_filesystem", {"host_id": host_id})
    assert "host not found" in _error(listing)
    worktrees = await _call(
        mcp_client,
        "list_host_worktrees",
        {"host_id": host_id, "path": "/tmp/oa-mcp-repo"},
    )
    assert "host not found" in _error(worktrees)


async def test_projects_and_usage(mcp_client: httpx.AsyncClient) -> None:
    """Create, read, rename, and delete a project. Usage stays readable throughout."""
    usage = _tool_text(await _call(mcp_client, "get_usage"))
    assert usage["object"] == "usage_report"

    dry = _tool_text(await _call(mcp_client, "create_project", {"name": "Gamma"}))
    assert dry["dry_run"] is True
    created = _tool_text(
        await _call(
            mcp_client,
            "create_project",
            {"name": "Gamma", "config": {"workspace": "/work/gamma"}, "confirm": True},
        )
    )
    project_id = created["id"]
    fetched = _tool_text(await _call(mcp_client, "get_project", {"project_id": project_id}))
    assert fetched["name"] == "Gamma"
    assert fetched["config"]["workspace"] == "/work/gamma"

    renamed = _tool_text(
        await _call(
            mcp_client,
            "update_project",
            {"project_id": project_id, "name": "Gamma 2", "confirm": True},
        )
    )
    assert renamed["name"] == "Gamma 2"
    kept = _tool_text(await _call(mcp_client, "delete_project", {"project_id": project_id}))
    assert kept["dry_run"] is True
    assert (
        _tool_text(await _call(mcp_client, "get_project", {"project_id": project_id}))["name"]
        == "Gamma 2"
    )

    removed = _tool_text(
        await _call(mcp_client, "delete_project", {"project_id": project_id, "confirm": True})
    )
    assert removed["deleted"] is True
    missing = await _call(mcp_client, "get_project", {"project_id": project_id})
    assert "404" in _error(missing)


async def test_session_policy_and_registry(mcp_client: httpx.AsyncClient) -> None:
    """A URL policy is created and deleted. The handler registry is readable."""
    session_id = await _session(mcp_client, "policy-ext")
    registry = _tool_text(await _call(mcp_client, "list_policy_registry"))
    assert registry["object"] == "list"
    assert isinstance(registry["data"], list)

    created = _tool_text(
        await _call(
            mcp_client,
            "create_session_policy",
            {
                "session_id": session_id,
                "name": "mcp-gate",
                "type": "url",
                "handler": "https://example.com/policies/eval",
                "confirm": True,
            },
        )
    )
    policy_id = created["id"]
    listed = _tool_text(
        await _call(mcp_client, "list_session_policies", {"session_id": session_id})
    )
    assert any(row.get("id") == policy_id for row in listed["data"])

    dry = _tool_text(
        await _call(
            mcp_client,
            "delete_session_policy",
            {"session_id": session_id, "policy_id": policy_id},
        )
    )
    assert dry["dry_run"] is True
    still = _tool_text(
        await _call(mcp_client, "list_session_policies", {"session_id": session_id})
    )
    assert any(row.get("id") == policy_id for row in still["data"])

    removed = _tool_text(
        await _call(
            mcp_client,
            "delete_session_policy",
            {"session_id": session_id, "policy_id": policy_id, "confirm": True},
        )
    )
    assert removed.get("deleted") is True or removed.get("id") == policy_id
    remaining = _tool_text(
        await _call(mcp_client, "list_session_policies", {"session_id": session_id})
    )
    assert all(row.get("id") != policy_id for row in remaining["data"])


async def test_session_mcp_server_round_trip(mcp_client: httpx.AsyncClient) -> None:
    """Declaring an HTTP MCP server mutates the session agent. Dry-run delete does not."""
    session_id = await _session(mcp_client, "mcp-servers-ext")
    created = _tool_text(
        await _call(
            mcp_client,
            "add_session_mcp_server",
            {
                "session_id": session_id,
                "name": "remote",
                "transport": "http",
                "url": "https://mcp.example.com/mcp",
                "confirm": True,
            },
        )
    )
    assert created["name"] == "remote"
    listed = _tool_text(
        await _call(mcp_client, "list_session_mcp_servers", {"session_id": session_id})
    )
    assert any(row["name"] == "remote" for row in listed["data"])

    dry = _tool_text(
        await _call(
            mcp_client,
            "delete_session_mcp_server",
            {"session_id": session_id, "name": "remote"},
        )
    )
    assert dry["dry_run"] is True
    still = _tool_text(
        await _call(mcp_client, "list_session_mcp_servers", {"session_id": session_id})
    )
    assert any(row["name"] == "remote" for row in still["data"])

    removed = _tool_text(
        await _call(
            mcp_client,
            "delete_session_mcp_server",
            {"session_id": session_id, "name": "remote", "confirm": True},
        )
    )
    assert removed == {"ok": True}
    empty = _tool_text(
        await _call(mcp_client, "list_session_mcp_servers", {"session_id": session_id})
    )
    assert all(row["name"] != "remote" for row in empty["data"])


async def test_harnesses_skills_and_extensions(mcp_client: httpx.AsyncClient) -> None:
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

    skills = await _call(mcp_client, "list_skills", {"session_id": session_id})
    assert "session host is not ready" in _error(skills)


async def test_sharing_dry_run_and_confirm(
    mcp_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dry-run leaves sharing alone. confirm writes the override, or the route refuses it."""
    monkeypatch.setenv("OMNIGENT_ADMIN_CREDENTIALS_PATH", str(tmp_path / "admin-creds"))
    before = _tool_text(await _call(mcp_client, "get_sharing"))
    assert before["object"] == "sharing"
    target = "read_only" if before["sharing_mode"] != "read_only" else "on"
    dry = _tool_text(await _call(mcp_client, "set_sharing", {"sharing_mode": target}))
    assert dry["dry_run"] is True
    assert dry["method"] == "PUT"
    assert (
        _tool_text(await _call(mcp_client, "get_sharing"))["sharing_mode"]
        == before["sharing_mode"]
    )

    response = await _call(mcp_client, "set_sharing", {"sharing_mode": target, "confirm": True})
    result = response.json()["result"]
    if result.get("isError") is True:
        assert "managed" in result["content"][0]["text"]
        assert (
            _tool_text(await _call(mcp_client, "get_sharing"))["sharing_mode"]
            == before["sharing_mode"]
        )
        return
    changed = json.loads(result["content"][0]["text"])
    assert changed["sharing_mode"] == target
    restored = _tool_text(
        await _call(
            mcp_client,
            "set_sharing",
            {"sharing_mode": before["sharing_mode"], "confirm": True},
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
                "grant_session_access",
                {
                    "session_id": session_id,
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
                "grant_session_access",
                {
                    "session_id": session_id,
                    "user_id": "alice@example.com",
                    "level": 1,
                    "confirm": True,
                },
            )
        )
        assert granted["user_id"] == "alice@example.com"
        assert granted["level"] == 1
        listed = _tool_text(
            await _call(client, "list_session_permissions", {"session_id": session_id})
        )
        assert any(row["user_id"] == "alice@example.com" for row in listed["permissions"])
        owner = _tool_text(await _call(client, "get_session_owner", {"session_id": session_id}))
        assert owner["owner"] == "local"

        kept = _tool_text(
            await _call(
                client,
                "revoke_session_access",
                {"session_id": session_id, "user_id": "alice@example.com"},
            )
        )
        assert kept["dry_run"] is True
        revoked = _tool_text(
            await _call(
                client,
                "revoke_session_access",
                {"session_id": session_id, "user_id": "alice@example.com", "confirm": True},
            )
        )
        assert revoked == {"ok": True}
        after = _tool_text(
            await _call(client, "list_session_permissions", {"session_id": session_id})
        )
        assert all(row["user_id"] != "alice@example.com" for row in after["permissions"])
