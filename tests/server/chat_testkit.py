"""Shared fixtures for team-chat and MCP bot-path tests.

Header auth identifies humans. Agent accounts use a real ``ogc_`` token when
the test asks for one. Rate-limit windows are cleared for each client.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from omnigent.db.db_models import SqlChatAgentAccount, SqlChatChannel, SqlChatMember, SqlUser
from omnigent.db.utils import get_or_create_engine, now_epoch_us
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.app import create_app
from omnigent.server.auth import UnifiedAuthProvider
from omnigent.server.chat_rate_limit import reset_chat_rate_limits
from omnigent.server.feature_flags import FEATURES_ENV_VAR
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.chat_store.sqlalchemy_store import SqlAlchemyChatStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore

ANNA = "anna@bloopark.de"
JENS = "jens@bloopark.de"
BOB = "bob@bloopark.de"
CARLA = "carla@bloopark.de"
AGENT_SECRET = "test-chat-agent-secret"
WEBHOOK_OK = "https://bot.example.com/hook"


def headers_for(user: str | None = None, token: str | None = None) -> dict[str, str]:
    """Auth headers for a human header identity or an ``ogc_`` bearer."""
    headers: dict[str, str] = {}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    elif user is not None:
        headers["X-Forwarded-Email"] = user
    return headers


def build_chat_app(db_uri: str, tmp_path: Path) -> FastAPI:
    """App with header auth. Feature flags come from ``OMNIGENT_FEATURES``."""
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    return create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=SqlAlchemyFileStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        artifact_store=artifact_store,
        agent_cache=AgentCache(artifact_store=artifact_store, cache_dir=tmp_path / "cache"),
        permission_store=SqlAlchemyPermissionStore(db_uri),
        auth_provider=UnifiedAuthProvider(source="header"),
    )


@pytest_asyncio.fixture()
async def chat_client(
    db_uri: str,
    runtime_init: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    """Client with team chat on and a configured agent-account secret."""
    del runtime_init
    monkeypatch.setenv(FEATURES_ENV_VAR, "team_chat")
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", AGENT_SECRET)
    reset_chat_rate_limits()
    transport = httpx.ASGITransport(app=build_chat_app(db_uri, tmp_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    reset_chat_rate_limits()


async def bootstrap(client: httpx.AsyncClient, user: str) -> dict:
    resp = await client.get("/v1/chat/bootstrap", headers=headers_for(user))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def channel_id(client: httpx.AsyncClient, user: str, name: str) -> str:
    if name == "general":
        payload = await bootstrap(client, user)
        (general,) = (row for row in payload["channels"] if row["name"] == "general")
        return general["id"]
    resp = await client.post("/v1/chat/channels", json={"name": name}, headers=headers_for(user))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def join(client: httpx.AsyncClient, user: str, channel: str) -> None:
    resp = await client.post(f"/v1/chat/channels/{channel}/join", headers=headers_for(user))
    assert resp.status_code == 204, resp.text


async def post_message(
    client: httpx.AsyncClient,
    user: str,
    channel: str,
    body: str,
    *,
    token: str | None = None,
    **extra: object,
) -> httpx.Response:
    return await client.post(
        f"/v1/chat/channels/{channel}/messages",
        json={"body": body, **extra},
        headers=headers_for(user, token),
    )


def ensure_live_user(db_uri: str, user_id: str) -> None:
    """Insert a live accounts row so a bot token can resolve its owner."""
    with Session(get_or_create_engine(db_uri)) as session:
        existing = session.execute(
            select(SqlUser).where(SqlUser.id == user_id)
        ).scalar_one_or_none()
        if existing is None:
            session.add(SqlUser(id=user_id, is_admin=False))
            session.commit()
        elif existing.deleted_at is not None:
            existing.deleted_at = None
            session.commit()


def _client_db_uri(client: httpx.AsyncClient) -> str | None:
    transport = getattr(client, "_transport", None)
    app = getattr(transport, "app", None)
    store = getattr(getattr(app, "state", None), "team_chat_store", None)
    location = getattr(store, "storage_location", None)
    return location if isinstance(location, str) else None


async def create_account(
    client: httpx.AsyncClient,
    owner: str,
    handle: str,
    *,
    display_name: str = "Grok",
    webhook_url: str = WEBHOOK_OK,
    ensure_owner: bool = True,
) -> httpx.Response:
    db_uri = _client_db_uri(client)
    if ensure_owner and db_uri is not None:
        ensure_live_user(db_uri, owner)
    return await client.post(
        "/v1/chat/agent-accounts",
        json={"display_name": display_name, "handle": handle, "webhook_url": webhook_url},
        headers=headers_for(owner),
    )


def add_member(db_uri: str, channel: str, principal_id: str) -> None:
    SqlAlchemyChatStore(db_uri).add_member(channel, principal_id)


def seed_session_channel(db_uri: str, *principals: str) -> str:
    """Insert a session-chat channel and optional memberships."""
    channel = uuid.uuid4().hex
    with Session(get_or_create_engine(db_uri)) as session:
        session.add(
            SqlChatChannel(
                id=channel,
                kind="session",
                visibility="public",
                session_root_id=uuid.uuid4().hex,
                created_at=now_epoch_us(),
            )
        )
        for principal in principals:
            session.add(
                SqlChatMember(
                    channel_id=channel,
                    principal_id=principal,
                    role="member",
                    joined_at=now_epoch_us(),
                )
            )
        session.commit()
    return channel


def agent_row(db_uri: str, account_id: str) -> SqlChatAgentAccount:
    with Session(get_or_create_engine(db_uri)) as session:
        row = session.get(SqlChatAgentAccount, (0, account_id))
        assert row is not None
        session.expunge(row)
        return row


async def rpc(
    client: httpx.AsyncClient,
    method: str,
    params: dict | None = None,
    *,
    token: str | None = None,
    user: str | None = None,
    protocol: str | None = None,
    rpc_id: int = 1,
) -> httpx.Response:
    """POST one JSON-RPC message to ``/mcp``."""
    headers = headers_for(user, token)
    if protocol is not None:
        headers["MCP-Protocol-Version"] = protocol
    body: dict = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        body["params"] = params
    return await client.post("/mcp", json=body, headers=headers)


async def tools_call(
    client: httpx.AsyncClient,
    name: str,
    arguments: dict,
    *,
    token: str | None = None,
    user: str | None = None,
    protocol: str | None = "2025-11-25",
) -> httpx.Response:
    return await rpc(
        client,
        "tools/call",
        {"name": name, "arguments": arguments},
        token=token,
        user=user,
        protocol=protocol,
    )


def tool_result(response: httpx.Response) -> dict:
    """Return the JSON-RPC result and require that it is not an RPC error."""
    payload = response.json()
    assert "error" not in payload, payload
    return payload["result"]


def tool_payload(response: httpx.Response) -> dict:
    """Return the tool's structured object, unwrapping the untrusted text."""
    import json

    result = tool_result(response)
    assert result.get("isError") is not True, result
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    text = result["content"][0]["text"]
    marker = "<untrusted_chat_content>"
    if marker in text:
        start = text.index(marker) + len(marker)
        end = text.rindex("</untrusted_chat_content>")
        return json.loads(text[start:end])
    return json.loads(text)
