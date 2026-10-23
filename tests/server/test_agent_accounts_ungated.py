"""Agent-account management without team chat (T-136, FR-112).

``/v1/agent-accounts`` is mounted regardless of ``team_chat``; the
``/v1/chat/agent-accounts`` alias keeps the chat router's feature gate.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from omnigent.db.db_models import SqlConversationItem
from omnigent.db.utils import get_or_create_engine
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.app import create_app
from omnigent.server.auth import LEVEL_OWNER, UnifiedAuthProvider
from omnigent.server.chat_rate_limit import reset_chat_rate_limits
from omnigent.server.feature_flags import FEATURES_ENV_VAR
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from omnigent.stores.project_store.sqlalchemy_store import SqlAlchemyProjectStore
from tests.server.chat_testkit import (
    AGENT_SECRET,
    JENS,
    WEBHOOK_OK,
    ensure_live_user,
    headers_for,
    rpc,
    tools_call,
)
from tests.server.test_mcp_agent_owner_equivalence import _outcome
from tests.server.test_mcp_endpoint import _bind_scripted, _unbind_scripted

UNGATED = "/v1/agent-accounts"
CHAT_ALIAS = "/v1/chat/agent-accounts"
MCP_PROTOCOL = "2025-11-25"


def _build_app(db_uri: str, tmp_path: Path) -> FastAPI:
    """The scope-test app (projects, hosts, header auth); flags come from the env."""
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    return create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=SqlAlchemyFileStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        artifact_store=artifact_store,
        agent_cache=AgentCache(artifact_store=artifact_store, cache_dir=tmp_path / "cache"),
        permission_store=SqlAlchemyPermissionStore(db_uri),
        project_store=SqlAlchemyProjectStore(db_uri),
        host_store=HostStore(db_uri),
        auth_provider=UnifiedAuthProvider(source="header"),
    )


@asynccontextmanager
async def _client_for(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture()
async def flag_off_client(
    db_uri: str,
    runtime_init: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    """Client with team_chat off and a configured agent-account secret."""
    del runtime_init
    monkeypatch.delenv(FEATURES_ENV_VAR, raising=False)
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", AGENT_SECRET)
    monkeypatch.setenv("OMNIGENT_AGENT_MCP_RPM", "10000")
    reset_chat_rate_limits()
    async with _client_for(_build_app(db_uri, tmp_path / "off")) as client:
        yield client
    reset_chat_rate_limits()


def _project(db_uri: str, owner: str, name: str) -> str:
    project_id = uuid.uuid4().hex
    SqlAlchemyProjectStore(db_uri).create(project_id, name, owner)
    return project_id


def _session(db_uri: str, owner: str, project_id: str | None) -> str:
    """A root session owned by *owner*, filed in *project_id*."""
    conversation = SqlAlchemyConversationStore(db_uri).create_conversation(
        kind="default", agent_id=uuid.uuid4().hex, project_id=project_id
    )
    permissions = SqlAlchemyPermissionStore(db_uri)
    permissions.ensure_user(owner)
    permissions.grant(owner, conversation.id, LEVEL_OWNER)
    return conversation.id


def _item_count(db_uri: str, session_id: str) -> int:
    with Session(get_or_create_engine(db_uri)) as session:
        return (
            session.scalar(
                select(func.count())
                .select_from(SqlConversationItem)
                .where(SqlConversationItem.conversation_id == session_id)
            )
            or 0
        )


def _account_body(handle: str, project_ids: list[str] | None = None) -> dict:
    body: dict = {"display_name": "PM bot", "handle": handle, "webhook_url": WEBHOOK_OK}
    if project_ids is not None:
        body["project_ids"] = project_ids
    return body


async def _create(
    client: httpx.AsyncClient, db_uri: str, base: str, handle: str, project_ids: list[str]
) -> httpx.Response:
    ensure_live_user(db_uri, JENS)
    return await client.post(
        base, json=_account_body(handle, project_ids), headers=headers_for(JENS)
    )


async def test_ac_113a_ungated_create_scope_rotate_with_team_chat_off(
    flag_off_client: httpx.AsyncClient, db_uri: str
) -> None:
    project = _project(db_uri, JENS, "FT Max")
    created = await _create(flag_off_client, db_uri, UNGATED, "pm-ungated", [project])
    assert created.status_code == 201, created.text
    account_id = created.json()["account"]["id"]
    old_token = created.json()["token"]
    assert old_token.startswith("ogc_")

    scoped = await flag_off_client.patch(
        f"{UNGATED}/{account_id}", json={"project_ids": [project]}, headers=headers_for(JENS)
    )
    rotated = await flag_off_client.post(
        f"{UNGATED}/{account_id}/rotate", headers=headers_for(JENS)
    )
    stale = await rpc(flag_off_client, "tools/list", token=old_token, protocol=MCP_PROTOCOL)

    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["project_ids"] == [project]
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["token"].startswith("ogc_")
    assert rotated.json()["token"] != old_token
    assert stale.status_code == 401, stale.text


async def test_ac_113b_chat_alias_is_404_with_team_chat_off(
    flag_off_client: httpx.AsyncClient, db_uri: str
) -> None:
    project = _project(db_uri, JENS, "FT Max")
    real = await _create(flag_off_client, db_uri, UNGATED, "pm-real", [project])
    assert real.status_code == 201, real.text
    account_id = real.json()["account"]["id"]
    jens = headers_for(JENS)

    created = await flag_off_client.post(
        CHAT_ALIAS, json=_account_body("pm-alias", [project]), headers=jens
    )
    scoped = await flag_off_client.patch(
        f"{CHAT_ALIAS}/{account_id}", json={"project_ids": [project]}, headers=jens
    )
    rotated = await flag_off_client.post(f"{CHAT_ALIAS}/{account_id}/rotate", headers=jens)
    missing = await flag_off_client.post(f"{CHAT_ALIAS}/{uuid.uuid4().hex}/rotate", headers=jens)

    for response in (created, scoped, rotated, missing):
        assert response.status_code == 404, (response.request.url, response.text)
    unchanged = await rpc(
        flag_off_client, "tools/list", token=real.json()["token"], protocol=MCP_PROTOCOL
    )
    assert unchanged.status_code == 200, unchanged.text


async def test_ac_113c_scoped_token_works_on_mcp_with_team_chat_off(
    flag_off_client: httpx.AsyncClient, db_uri: str
) -> None:
    project_a = _project(db_uri, JENS, "FT Max")
    project_b = _project(db_uri, JENS, "ProductOS")
    in_scope = _session(db_uri, JENS, project_a)
    out_of_scope = _session(db_uri, JENS, project_b)
    created = await _create(flag_off_client, db_uri, UNGATED, "pm-scoped", [project_a])
    assert created.status_code == 201, created.text
    token = created.json()["token"]

    read = await tools_call(flag_off_client, "get_session", {"session_id": in_scope}, token=token)
    assert _outcome(read) == ("ok",), read.text
    sessions_module, original, runner = await _bind_scripted(in_scope, "reply")
    try:
        sent = await tools_call(
            flag_off_client,
            "send_message",
            {"session_id": in_scope, "text": "hi", "confirm": True},
            token=token,
        )
    finally:
        await _unbind_scripted(sessions_module, original, runner)
    assert _outcome(sent) == ("ok",), sent.text
    assert _item_count(db_uri, in_scope) > 0

    for name, arguments in (
        ("get_session", {"session_id": out_of_scope}),
        ("send_message", {"session_id": out_of_scope, "text": "hi", "confirm": True}),
    ):
        response = await tools_call(flag_off_client, name, arguments, token=token)
        assert _outcome(response) == ("error", "not_found"), (name, response.text)
    assert _item_count(db_uri, out_of_scope) == 0

    listed = await rpc(flag_off_client, "tools/list", token=token, protocol=MCP_PROTOCOL)
    assert listed.status_code == 200, listed.text
    names = [tool["name"] for tool in listed.json()["result"]["tools"]]
    assert names
    assert [name for name in names if name.startswith("chat_")] == []


async def test_ac_113d_agent_token_cannot_create_on_ungated_route_flag_off_or_on(
    flag_off_client: httpx.AsyncClient,
    db_uri: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(db_uri, JENS, "FT Max")
    created = await _create(flag_off_client, db_uri, UNGATED, "pm-token", [project])
    assert created.status_code == 201, created.text
    bot = headers_for(token=created.json()["token"])

    bot_off = await flag_off_client.post(
        UNGATED, json=_account_body("bot-made-off", [project]), headers=bot
    )
    human_off = await flag_off_client.post(
        UNGATED, json=_account_body("human-made-off", [project]), headers=headers_for(JENS)
    )
    assert bot_off.status_code == 401, bot_off.text
    assert human_off.status_code == 201, human_off.text

    monkeypatch.setenv(FEATURES_ENV_VAR, "team_chat")
    async with _client_for(_build_app(db_uri, tmp_path / "on")) as flag_on_client:
        bot_on = await flag_on_client.post(
            UNGATED, json=_account_body("bot-made-on", [project]), headers=bot
        )
        human_on = await flag_on_client.post(
            UNGATED, json=_account_body("human-made-on", [project]), headers=headers_for(JENS)
        )
    assert bot_on.status_code == 401, bot_on.text
    assert human_on.status_code == 201, human_on.text
