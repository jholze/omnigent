"""Agent handles from ``GET /v1/chat/agents`` and READ grants on channel join."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import update

from omnigent.db.db_models import SqlAgent
from omnigent.server.auth import LEVEL_OWNER, LEVEL_READ
from omnigent.server.feature_flags import FEATURES_ENV_VAR
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.chat_store.sqlalchemy_store import SqlAlchemyChatStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from tests.server.chat_testkit import (
    ANNA,
    CARLA,
    JENS,
    build_chat_app,
    channel_id,
    chat_client,
    create_account,
    headers_for,
    join,
    post_message,
)
from tests.server.test_chat_agent_threads import (
    _conversation_count,
    _create_agent,
    _create_project,
    _session,
    _set_project_name,
    _stop_agent_threads,
    _system_rows,
    _thread,
    _wait_for_start,
    runner_202,
    sharing_off_client,
)

_ = (chat_client, runner_202, _stop_agent_threads, sharing_off_client)

MENTION = "<@omnigent_agent:agent>"


def _store_bundle(agent_id: str) -> str:
    """Put a loadable bundle in the runtime artifact store and return its key."""
    from omnigent.runtime import get_artifact_store
    from tests.server.helpers import build_agent_bundle

    key = f"agents/{agent_id}/bundle.tar.gz"
    artifact_store = get_artifact_store()
    assert artifact_store is not None
    artifact_store.put(key, build_agent_bundle("coder"))
    return key


def _set_created_at(db_uri: str, agent_id: str, created_at: int) -> None:
    with _session(db_uri) as session:
        session.execute(
            update(SqlAgent).where(SqlAgent.id == agent_id).values(created_at=created_at)
        )
        session.commit()


def _server_agent(db_uri: str, agent_id: str, name: str, created_at: int) -> str:
    """Server agent visible to every caller, with a fixed ``created_at``."""
    SqlAlchemyAgentStore(db_uri).create(agent_id, name, _store_bundle(agent_id))
    _set_created_at(db_uri, agent_id, created_at)
    return agent_id


async def _chat_agents(client: httpx.AsyncClient, user: str) -> dict:
    response = await client.get("/v1/chat/agents", headers=headers_for(user))
    assert response.status_code == 200, response.text
    return response.json()


def _handle_by_agent_id(payload: dict) -> dict[str, str]:
    return {row["agent_id"]: row["handle"] for row in payload["omnigent_agents"]}


@pytest_asyncio.fixture()
async def restricted_sharing_client(
    db_uri: str,
    runtime_init: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    """Chat client whose join grant checks the session workspace first."""
    del runtime_init
    monkeypatch.setattr(
        "omnigent.server.sharing_settings.read_sharing_mode_override",
        lambda: None,
    )
    monkeypatch.setenv(FEATURES_ENV_VAR, "team_chat")
    monkeypatch.setenv("OMNIGENT_SHARING_MODE", "restricted_read_only")
    transport = httpx.ASGITransport(app=build_chat_app(db_uri, tmp_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _billing_agent_session(client: httpx.AsyncClient, db_uri: str) -> tuple[str, str]:
    """Anna opens an agent thread in #billing; return the channel and its session S7."""
    agent_id = _create_agent(db_uri)
    billing = await channel_id(client, ANNA, "billing")
    _create_project(db_uri, ANNA, "billing", {"agent_id": agent_id})
    _set_project_name(db_uri, billing, "billing")
    posted = await post_message(client, ANNA, billing, f"{MENTION} schau mal")
    assert posted.status_code == 201, posted.text
    await _wait_for_start()
    session_id = _thread(db_uri, posted.json()["id"]).session_id
    assert session_id
    return billing, session_id


async def test_ac_039a_slug_collision_suffix_follows_created_at(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    # Insert the newer agent first, with the lower id, so neither insert nor id order matches.
    newer = _server_agent(db_uri, "0" + uuid.uuid4().hex[1:], "coder deepseek", created_at=2_000)
    older = _server_agent(db_uri, "f" + uuid.uuid4().hex[1:], "Coder DeepSeek", created_at=1_000)

    handles = _handle_by_agent_id(await _chat_agents(chat_client, ANNA))

    assert handles[older] == "coder-deepseek"
    assert handles[newer] == "coder-deepseek-2"


async def test_ac_039a_agent_named_agent_does_not_take_the_alias_handle(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    named_agent = _server_agent(db_uri, uuid.uuid4().hex, "Agent", created_at=1_000)

    handles = _handle_by_agent_id(await _chat_agents(chat_client, ANNA))

    assert named_agent in handles
    assert handles[named_agent] != "agent"


async def test_ac_039a_active_agent_account_is_listed(chat_client: httpx.AsyncClient) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    assert created.status_code == 201, created.text

    payload = await _chat_agents(chat_client, ANNA)

    assert "grok-jens" in [row["handle"] for row in payload["agent_accounts"]]


async def test_ac_039a_revoked_agent_account_is_not_listed(chat_client: httpx.AsyncClient) -> None:
    created = await create_account(chat_client, JENS, "grok-old")
    assert created.status_code == 201, created.text
    revoked = await chat_client.post(
        f"/v1/chat/agent-accounts/{created.json()['account']['id']}/revoke",
        headers=headers_for(JENS),
    )
    assert revoked.status_code == 204, revoked.text

    payload = await _chat_agents(chat_client, ANNA)

    assert "grok-old" not in [row["handle"] for row in payload["agent_accounts"]]


async def test_ac_039b_agent_the_caller_cannot_see_is_absent(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    hidden = uuid.uuid4().hex
    SqlAlchemyAgentStore(db_uri).create_user_agent(
        hidden, "Secret Planner", _store_bundle(hidden), owner=JENS
    )

    payload = await _chat_agents(chat_client, ANNA)

    assert hidden not in _handle_by_agent_id(payload)
    assert "secret-planner" not in [row["handle"] for row in payload["omnigent_agents"]]


async def test_ac_039b_mention_of_hidden_handle_is_agent_not_found(
    chat_client: httpx.AsyncClient, db_uri: str, runner_202: None
) -> None:
    hidden = uuid.uuid4().hex
    SqlAlchemyAgentStore(db_uri).create_user_agent(
        hidden, "Secret Planner", _store_bundle(hidden), owner=JENS
    )
    # A working project agent, so a fallback start would create a session.
    project_agent = _create_agent(db_uri)
    billing = await channel_id(chat_client, ANNA, "billing")
    _create_project(db_uri, ANNA, "billing", {"agent_id": project_agent})
    _set_project_name(db_uri, billing, "billing")
    before = _conversation_count(db_uri)

    posted = await post_message(
        chat_client, ANNA, billing, "<@omnigent_agent:secret-planner> schau mal"
    )
    assert posted.status_code == 201, posted.text
    await _wait_for_start()

    assert _conversation_count(db_uri) == before
    assert "agent_not_found" in [code for _body, code in _system_rows(db_uri, posted.json()["id"])]


async def test_ac_086a_join_grants_read_on_existing_agent_thread_session(
    chat_client: httpx.AsyncClient, db_uri: str, runner_202: None
) -> None:
    billing, s7 = await _billing_agent_session(chat_client, db_uri)
    permissions = SqlAlchemyPermissionStore(db_uri)
    assert permissions.get_permission_level(CARLA, s7) is None

    await join(chat_client, CARLA, billing)

    assert permissions.get_permission_level(CARLA, s7) == LEVEL_READ


async def test_ac_086a_leave_keeps_the_join_read_grant(
    chat_client: httpx.AsyncClient, db_uri: str, runner_202: None
) -> None:
    billing, s7 = await _billing_agent_session(chat_client, db_uri)
    await join(chat_client, CARLA, billing)

    left = await chat_client.post(f"/v1/chat/channels/{billing}/leave", headers=headers_for(CARLA))
    assert left.status_code == 204, left.text

    assert SqlAlchemyPermissionStore(db_uri).get_permission_level(CARLA, s7) == LEVEL_READ


async def test_ac_086a_rejoin_does_not_lower_existing_owner_grant(
    chat_client: httpx.AsyncClient, db_uri: str, runner_202: None
) -> None:
    billing, s7 = await _billing_agent_session(chat_client, db_uri)
    permissions = SqlAlchemyPermissionStore(db_uri)
    assert permissions.get_permission_level(ANNA, s7) == LEVEL_OWNER

    left = await chat_client.post(f"/v1/chat/channels/{billing}/leave", headers=headers_for(ANNA))
    assert left.status_code == 204, left.text
    assert permissions.get_permission_level(ANNA, s7) == LEVEL_OWNER

    await join(chat_client, ANNA, billing)

    assert permissions.get_permission_level(ANNA, s7) == LEVEL_OWNER


async def test_ac_039a_public_agent_accounts_omit_credential_fields(
    chat_client: httpx.AsyncClient,
) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    assert created.status_code == 201, created.text

    payload = await _chat_agents(chat_client, ANNA)

    row = next(row for row in payload["agent_accounts"] if row["handle"] == "grok-jens")
    assert set(row) == {"id", "handle", "display_name", "owner_label"}
    assert row["owner_label"] == "Jens"
    owned = await chat_client.get("/v1/chat/agent-accounts", headers=headers_for(JENS))
    assert owned.status_code == 200, owned.text
    owned_row = next(item for item in owned.json() if item["handle"] == "grok-jens")
    assert owned_row["token_prefix"]


async def test_ac_086a_sharing_off_join_grants_no_read(
    sharing_off_client: httpx.AsyncClient, db_uri: str, runner_202: None
) -> None:
    billing, s7 = await _billing_agent_session(sharing_off_client, db_uri)
    permissions = SqlAlchemyPermissionStore(db_uri)
    assert permissions.get_permission_level(CARLA, s7) is None

    joined = await sharing_off_client.post(
        f"/v1/chat/channels/{billing}/join", headers=headers_for(CARLA)
    )

    assert joined.status_code == 204, joined.text
    assert SqlAlchemyChatStore(db_uri).is_member(billing, CARLA)
    assert permissions.get_permission_level(CARLA, s7) is None


async def test_ac_086a_join_stays_204_when_the_workspace_lookup_fails(
    restricted_sharing_client: httpx.AsyncClient,
    db_uri: str,
    runner_202: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    billing, s7 = await _billing_agent_session(restricted_sharing_client, db_uri)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("conversation store unavailable")

    monkeypatch.setattr(
        "omnigent.stores.conversation_store.sqlalchemy_store."
        "SqlAlchemyConversationStore.get_conversation",
        fail,
    )

    joined = await restricted_sharing_client.post(
        f"/v1/chat/channels/{billing}/join", headers=headers_for(CARLA)
    )

    assert joined.status_code == 204, joined.text
    assert SqlAlchemyChatStore(db_uri).is_member(billing, CARLA)
    assert SqlAlchemyPermissionStore(db_uri).get_permission_level(CARLA, s7) is None
