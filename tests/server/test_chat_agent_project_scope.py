"""Project-scoped agent accounts (T-134, ADR-0011, FR-105..FR-111, NFR-033/034).

A scoped ``ogc_`` bot has its owner's rights intersected with sessions whose
root session is filed in an allowed project. Unscoped accounts keep ADR-0010.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import omnigent.debug_logging as debug_logging
from omnigent.db.db_models import (
    SqlChatAgentAccount,
    SqlChatChannel,
    SqlChatMember,
    SqlChatMessage,
    SqlChatThread,
    SqlConversationItem,
)
from omnigent.db.utils import get_or_create_engine, now_epoch_us
from omnigent.host.frames import HARNESS_NOT_CONFIGURED_ERROR_CODE, HostHelloFrame
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.app import create_app
from omnigent.server.auth import LEVEL_OWNER, UnifiedAuthProvider
from omnigent.server.chat_rate_limit import reset_chat_rate_limits
from omnigent.server.feature_flags import FEATURES_ENV_VAR
from omnigent.server.routes._sessions.helpers import _HostLaunchAttempt
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.chat_store.sqlalchemy_store import SqlAlchemyChatStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from omnigent.stores.project_store.sqlalchemy_store import SqlAlchemyProjectStore
from tests.server.chat_testkit import (
    AGENT_SECRET,
    ANNA,
    JENS,
    WEBHOOK_OK,
    agent_row,
    ensure_live_user,
    headers_for,
    rpc,
    tools_call,
)
from tests.server.test_chat_agent_threads import (  # noqa: F401 - autouse teardown
    MENTION,
    _set_project_name,
    _stop_agent_threads,
    _system_rows,
    _thread,
    _wait_for_start,
)
from tests.server.test_mcp_agent_owner_equivalence import (
    _conversation_count,
    _outcome,
    _register_agent,
)

FT_MAX = "Fishing Tackle Max"
PRODUCT_OS = "ProductOS"
MAC_HOST = "c2d81b1a6812ae1cf32221c5a2a70ba0"


@pytest_asyncio.fixture()
async def scope_client(
    db_uri: str,
    runtime_init: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    """Team-chat client whose app also mounts first-class projects and hosts."""
    del runtime_init
    monkeypatch.setenv(FEATURES_ENV_VAR, "team_chat")
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", AGENT_SECRET)
    monkeypatch.setenv("OMNIGENT_AGENT_MCP_RPM", "10000")
    reset_chat_rate_limits()
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    app = create_app(
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
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    reset_chat_rate_limits()


@dataclass
class _World:
    """Jens' projects and sessions plus a bot scoped to Fishing Tackle Max."""

    client: httpx.AsyncClient
    db_uri: str
    ft_max: str
    product_os: str
    ft_session: str
    pos_session: str
    loose_session: str
    account: dict
    token: str
    webhook_secret: str


def _project(db_uri: str, owner: str, name: str) -> str:
    project_id = uuid.uuid4().hex
    SqlAlchemyProjectStore(db_uri).create(project_id, name, owner)
    return project_id


def _session(
    db_uri: str,
    owner: str,
    project_id: str | None,
    *,
    parent: str | None = None,
    agent_id: str | None = None,
) -> str:
    """A listed session owned by *owner*, filed in *project_id* (roots only)."""
    store = SqlAlchemyConversationStore(db_uri)
    conversation = store.create_conversation(
        kind="sub_agent" if parent else "default",
        agent_id=agent_id or uuid.uuid4().hex,
        parent_conversation_id=parent,
        project_id=project_id,
    )
    permissions = SqlAlchemyPermissionStore(db_uri)
    permissions.ensure_user(owner)
    permissions.grant(owner, conversation.id, LEVEL_OWNER)
    return conversation.id


def _project_of(db_uri: str, session_id: str) -> str | None:
    conversation = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert conversation is not None
    return conversation.project_id


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


def _account_count(db_uri: str) -> int:
    with Session(get_or_create_engine(db_uri)) as session:
        return session.scalar(select(func.count()).select_from(SqlChatAgentAccount)) or 0


async def _create_account(
    client: httpx.AsyncClient, db_uri: str, handle: str, project_ids: list[str] | None
) -> httpx.Response:
    ensure_live_user(db_uri, JENS)
    body: dict = {"display_name": "PM - FT Max", "handle": handle, "webhook_url": WEBHOOK_OK}
    if project_ids is not None:
        body["project_ids"] = project_ids
    return await client.post("/v1/chat/agent-accounts", json=body, headers=headers_for(JENS))


async def _set_scope(
    client: httpx.AsyncClient, account_id: str, project_ids: list[str] | None
) -> httpx.Response:
    return await client.patch(
        f"/v1/chat/agent-accounts/{account_id}",
        json={"project_ids": project_ids},
        headers=headers_for(JENS),
    )


def _text_payload(response: httpx.Response) -> dict:
    assert _outcome(response) == ("ok",), response.text
    result = response.json()["result"]
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and "data" in structured:
        return structured
    return json.loads(result["content"][0]["text"])


async def _listed_ids(client: httpx.AsyncClient, *, token: str | None = None, user=None) -> set:
    listed = await tools_call(client, "list_sessions", {"limit": 100}, token=token, user=user)
    return {row["id"] for row in _text_payload(listed)["data"]}


@pytest_asyncio.fixture()
async def world(scope_client: httpx.AsyncClient, db_uri: str) -> _World:
    ft_max = _project(db_uri, JENS, FT_MAX)
    product_os = _project(db_uri, JENS, PRODUCT_OS)
    created = await _create_account(scope_client, db_uri, "pm-ft-max", [ft_max])
    assert created.status_code == 201, created.text
    return _World(
        client=scope_client,
        db_uri=db_uri,
        ft_max=ft_max,
        product_os=product_os,
        ft_session=_session(db_uri, JENS, ft_max),
        pos_session=_session(db_uri, JENS, product_os),
        loose_session=_session(db_uri, JENS, None),
        account=created.json()["account"],
        token=created.json()["token"],
        webhook_secret=created.json()["webhook_secret"],
    )


def _session_channel(db_uri: str, session_id: str, *members: str) -> str:
    channel = uuid.uuid4().hex
    with Session(get_or_create_engine(db_uri)) as session:
        session.add(
            SqlChatChannel(
                id=channel,
                kind="session",
                visibility="public",
                session_root_id=session_id,
                created_at=now_epoch_us(),
            )
        )
        for member in members:
            session.add(
                SqlChatMember(
                    channel_id=channel,
                    principal_id=member,
                    role="member",
                    joined_at=now_epoch_us(),
                )
            )
        session.commit()
    return channel


async def test_ac_105a_scoped_list_sessions_returns_only_the_allowed_project(world) -> None:
    ids = await _listed_ids(world.client, token=world.token)

    assert ids == {world.ft_session}


async def test_ac_105b_out_of_scope_session_is_404_and_nothing_is_sent(world) -> None:
    for name, arguments in (
        ("get_session", {"session_id": world.pos_session}),
        ("get_session_items", {"session_id": world.pos_session}),
        ("send_message", {"session_id": world.pos_session, "text": "hi", "confirm": True}),
    ):
        response = await tools_call(world.client, name, arguments, token=world.token)
        assert _outcome(response) == ("error", "not_found"), (name, response.text)

    assert _item_count(world.db_uri, world.pos_session) == 0


async def test_ac_105c_allowed_session_read_and_post_succeed_at_owner_level(world) -> None:
    arguments = {"session_id": world.ft_session}
    bot = await tools_call(world.client, "get_session", arguments, token=world.token)
    owner = await tools_call(world.client, "get_session", arguments, user=JENS)
    assert _outcome(bot) == _outcome(owner) == ("ok",)
    channel = _session_channel(world.db_uri, world.ft_session, JENS)

    posted = await world.client.post(
        f"/v1/chat/channels/{channel}/messages",
        json={"body": "FT Max status"},
        headers=headers_for(token=world.token),
    )
    read = await world.client.get(
        f"/v1/chat/channels/{channel}/messages", headers=headers_for(token=world.token)
    )

    assert posted.status_code == 201, posted.text
    assert read.status_code == 200, read.text
    assert "FT Max status" in read.text


async def test_ac_105d_scoped_list_projects_returns_only_the_allowed_project(world) -> None:
    listed = await tools_call(world.client, "list_projects", {}, token=world.token)

    rows = _text_payload(listed)["data"]
    assert [(row["id"], row["name"]) for row in rows] == [(world.ft_max, FT_MAX)]


async def test_ac_106a_create_session_in_the_allowed_project(world) -> None:
    _register_agent(world.db_uri, "coder")

    made = await tools_call(
        world.client,
        "create_session",
        {"agent_name": "coder", "project_id": world.ft_max, "confirm": True},
        token=world.token,
    )

    assert _outcome(made) == ("ok",), made.text
    session = json.loads(made.json()["result"]["content"][0]["text"])["session"]
    assert _project_of(world.db_uri, session["id"]) == world.ft_max


async def test_ac_106b_create_without_or_with_another_project_is_refused(world) -> None:
    _register_agent(world.db_uri, "coder")
    before = _conversation_count(world.db_uri)

    missing = await tools_call(
        world.client, "create_session", {"agent_name": "coder", "confirm": True}, token=world.token
    )
    other = await tools_call(
        world.client,
        "create_session",
        {"agent_name": "coder", "project_id": world.product_os, "confirm": True},
        token=world.token,
    )

    assert _outcome(missing) == ("error", "out_of_scope"), missing.text
    assert _outcome(other) == ("error", "not_found"), other.text
    assert _conversation_count(world.db_uri) == before


async def _bot_fork(world: _World, source_id: str, agent_id: str) -> httpx.Response:
    """The scoped bot forks *source_id* (POST /v1/sessions/{id}/fork in-process)."""
    return await tools_call(
        world.client,
        "manage_session",
        {"action": "fork", "session_id": source_id, "agent_id": agent_id, "confirm": True},
        token=world.token,
    )


async def test_ac_106b_fork_of_child_of_allowed_root_is_refused(world) -> None:
    agent_id = _register_agent(world.db_uri, "coder")
    child = _session(world.db_uri, JENS, None, parent=world.ft_session, agent_id=agent_id)
    before = _conversation_count(world.db_uri)

    forked = await _bot_fork(world, child, agent_id)

    assert _outcome(forked) == ("error", "out_of_scope"), forked.text
    assert _conversation_count(world.db_uri) == before


async def test_ac_106b_fork_of_allowed_root_stays_in_that_project(world) -> None:
    agent_id = _register_agent(world.db_uri, "coder")

    forked = await _bot_fork(world, world.ft_session, agent_id)

    assert _outcome(forked) == ("ok",), forked.text
    fork_id = _text_payload_object(forked)["id"]
    assert fork_id != world.ft_session
    assert _project_of(world.db_uri, fork_id) == world.ft_max


def _text_payload_object(response: httpx.Response) -> dict:
    """The tool's JSON object, from structured content or the text block."""
    result = response.json()["result"]
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and "id" in structured:
        return structured
    return json.loads(result["content"][0]["text"])


async def test_ac_106c_moving_an_allowed_session_out_is_refused(world) -> None:
    for target in (world.product_os, ""):
        moved = await tools_call(
            world.client,
            "manage_session",
            {
                "action": "update",
                "session_id": world.ft_session,
                "project_id": target,
                "confirm": True,
            },
            token=world.token,
        )
        assert _outcome(moved)[0] == "error", (target, moved.text)
        assert _project_of(world.db_uri, world.ft_session) == world.ft_max


async def test_ac_107a_child_of_an_allowed_root_can_be_read_and_spawned(world) -> None:
    child = _session(world.db_uri, JENS, None, parent=world.ft_session)
    agent_id = _register_agent(world.db_uri, "coder")

    read = await tools_call(world.client, "get_session", {"session_id": child}, token=world.token)
    spawned = await tools_call(
        world.client,
        "create_session",
        {"agent_id": agent_id, "parent_session_id": world.ft_session, "confirm": True},
        token=world.token,
    )

    assert _outcome(read) == ("ok",), read.text
    assert _outcome(spawned) == ("ok",), spawned.text
    session = json.loads(spawned.json()["result"]["content"][0]["text"])["session"]
    assert session["parent_session_id"] == world.ft_session


async def test_ac_107b_child_of_an_out_of_scope_root_is_404(world) -> None:
    child = _session(world.db_uri, JENS, None, parent=world.pos_session)

    read = await tools_call(world.client, "get_session", {"session_id": child}, token=world.token)

    assert _outcome(read) == ("error", "not_found"), read.text


async def test_ac_108a_session_moved_by_the_owner_is_404_on_the_next_request(world) -> None:
    arguments = {"session_id": world.ft_session}
    before = await tools_call(world.client, "get_session", arguments, token=world.token)
    assert _outcome(before) == ("ok",), before.text

    moved = await world.client.patch(
        f"/v1/sessions/{world.ft_session}",
        json={"project_id": world.product_os},
        headers=headers_for(JENS),
    )
    assert moved.status_code == 200, moved.text
    after = await tools_call(world.client, "get_session", arguments, token=world.token)

    assert _outcome(after) == ("error", "not_found"), after.text


async def test_ac_108b_revoke_or_rotate_makes_the_old_token_401(world) -> None:
    second = await _create_account(world.client, world.db_uri, "pm-second", [world.ft_max])
    assert second.status_code == 201, second.text

    rotated = await world.client.post(
        f"/v1/chat/agent-accounts/{world.account['id']}/rotate", headers=headers_for(JENS)
    )
    revoked = await world.client.post(
        f"/v1/chat/agent-accounts/{second.json()['account']['id']}/revoke",
        headers=headers_for(JENS),
    )
    assert rotated.status_code == 200, rotated.text
    assert revoked.status_code == 204, revoked.text

    for token in (world.token, second.json()["token"]):
        response = await rpc(world.client, "tools/list", token=token, protocol="2025-11-25")
        assert response.status_code == 401, response.text


async def test_ac_108c_adding_a_project_widens_the_next_list(world) -> None:
    widened = await _set_scope(world.client, world.account["id"], [world.ft_max, world.product_os])
    assert widened.status_code == 200, widened.text

    ids = await _listed_ids(world.client, token=world.token)

    assert ids == {world.ft_session, world.pos_session}


async def test_ac_109a_unscoped_account_lists_the_owners_sessions(world) -> None:
    created = await _create_account(world.client, world.db_uri, "grok-unscoped", None)
    assert created.status_code == 201, created.text
    assert created.json()["account"]["project_ids"] is None

    bot_ids = await _listed_ids(world.client, token=created.json()["token"])
    owner_ids = await _listed_ids(world.client, user=JENS)

    assert bot_ids == owner_ids
    assert {world.ft_session, world.pos_session, world.loose_session} <= bot_ids


async def _owner_state(world: _World) -> dict:
    """What the forbidden calls could change, read as Jens."""
    jens = headers_for(JENS)
    projects = await world.client.get("/v1/projects", headers=jens)
    order = await world.client.get("/v1/projects/order", headers=jens)
    sharing = await world.client.get("/v1/sharing", headers=jens)
    policies = await world.client.get("/v1/policies", headers=jens)
    accounts = await world.client.get("/v1/chat/agent-accounts", headers=jens)
    grants = await world.client.get(f"/v1/sessions/{world.ft_session}/permissions", headers=jens)
    return {
        "projects": projects.json(),
        "order": order.json(),
        "sharing": sharing.json(),
        "policies": policies.json(),
        # last_seen_at moves on every bot call; the account itself must not.
        "accounts": [
            {key: value for key, value in row.items() if key != "last_seen_at"}
            for row in accounts.json()
        ],
        "grants": grants.json(),
    }


def _forbidden_calls(world: _World) -> list[tuple[str, dict]]:
    confirm = {"confirm": True}
    return [
        ("project", {"action": "create", "name": "Bot project", **confirm}),
        ("project", {"action": "update", "project_id": world.ft_max, "name": "x", **confirm}),
        ("project", {"action": "delete", "project_id": world.ft_max, **confirm}),
        ("project", {"action": "order", "ordered_project_ids": [world.ft_max], **confirm}),
        ("sharing", {"action": "set", "public_sharing": True, **confirm}),
        (
            "session_access",
            {
                "action": "grant",
                "session_id": world.ft_session,
                "user_id": ANNA,
                "level": 1,
                **confirm,
            },
        ),
        (
            "policy",
            {
                "action": "create",
                "scope": "global",
                "name": "bot-policy",
                "type": "python",
                "handler": "omnigent.policies.noop",
                **confirm,
            },
        ),
        ("list_hosts", confirm),
        ("host_fs", {"action": "mkdir", "host_id": MAC_HOST, "path": "bot-dir", **confirm}),
    ]


async def test_ac_110a_forbidden_endpoint_classes_are_403_out_of_scope(world) -> None:
    before = await _owner_state(world)

    for name, arguments in _forbidden_calls(world):
        response = await tools_call(world.client, name, arguments, token=world.token)
        assert _outcome(response) == ("error", "out_of_scope"), (name, arguments, response.text)
    managed = await world.client.post(
        "/v1/chat/agent-accounts",
        json={"display_name": "Other", "handle": "other-bot", "webhook_url": WEBHOOK_OK},
        headers=headers_for(token=world.token),
    )
    assert managed.status_code == 403, managed.text
    assert managed.json()["error"]["code"] == "out_of_scope"

    assert await _owner_state(world) == before


async def test_ac_110b_send_on_an_allowed_host_bound_session_launches_on_that_host(
    world, scope_client, monkeypatch
) -> None:
    agent_id = _register_agent(world.db_uri, "coder")
    session_id = _session(world.db_uri, JENS, world.ft_max, agent_id=agent_id)
    SqlAlchemyConversationStore(world.db_uri).set_host_id(
        session_id, host_id=MAC_HOST, workspace="/Users/jens/ft-max"
    )
    HostStore(world.db_uri).upsert_on_connect(MAC_HOST, "jens-mac", JENS)
    app = scope_client._transport.app  # type: ignore[attr-defined]
    app.state.host_registry.register(
        MAC_HOST,
        AsyncMock(),
        HostHelloFrame(version="0.1.0-test", frame_protocol_version=1, name="jens-mac"),
        owner=JENS,
    )
    launches: list[tuple[str, str | None]] = []

    async def record_launch(conv, *_args, **_kwargs) -> _HostLaunchAttempt:
        launches.append((conv.id, conv.host_id))
        return _HostLaunchAttempt(
            runner_id="", error_code=HARNESS_NOT_CONFIGURED_ERROR_CODE, error="stop here"
        )

    monkeypatch.setattr("omnigent.server.routes.sessions._launch_runner_on_host", record_launch)

    sent = await tools_call(
        world.client,
        "send_message",
        {"session_id": session_id, "text": "hi", "confirm": True},
        token=world.token,
    )

    assert _outcome(sent) != ("error", "out_of_scope"), sent.text
    assert launches == [(session_id, MAC_HOST)], sent.text


async def test_ac_110c_workspace_shell_on_an_allowed_session_is_refused(world) -> None:
    shell = await tools_call(
        world.client,
        "session_workspace",
        {"action": "shell", "session_id": world.ft_session, "command": "true", "confirm": True},
        token=world.token,
    )

    assert _outcome(shell) == ("error", "out_of_scope"), shell.text


async def test_ac_111a_empty_project_ids_is_400_and_changes_nothing(world) -> None:
    accounts_before = _account_count(world.db_uri)
    created = await _create_account(world.client, world.db_uri, "empty-scope", [])
    edited = await _set_scope(world.client, world.account["id"], [])

    assert created.status_code == 400, created.text
    assert edited.status_code == 400, edited.text
    assert _account_count(world.db_uri) == accounts_before
    assert json.loads(agent_row(world.db_uri, world.account["id"]).project_ids) == [world.ft_max]


async def test_ac_111b_a_project_the_owner_does_not_own_is_404_and_changes_nothing(
    world,
) -> None:
    annas = _project(world.db_uri, ANNA, "Anna private")
    accounts_before = _account_count(world.db_uri)
    created = await _create_account(world.client, world.db_uri, "foreign-scope", [annas])
    edited = await _set_scope(world.client, world.account["id"], [world.ft_max, annas])

    assert created.status_code == 404, created.text
    assert edited.status_code == 404, edited.text
    assert _account_count(world.db_uri) == accounts_before
    assert json.loads(agent_row(world.db_uri, world.account["id"]).project_ids) == [world.ft_max]


async def test_ac_111c_deleted_project_leaves_an_empty_list_that_denies_everything(
    world,
) -> None:
    wide = await _create_account(
        world.client, world.db_uri, "pm-wide", [world.ft_max, world.product_os]
    )
    assert wide.status_code == 201, wide.text

    deleted = await world.client.delete(f"/v1/projects/{world.ft_max}", headers=headers_for(JENS))
    assert deleted.status_code in (200, 204), deleted.text

    assert agent_row(world.db_uri, world.account["id"]).project_ids == "[]"
    assert json.loads(agent_row(world.db_uri, wide.json()["account"]["id"]).project_ids) == [
        world.product_os
    ]
    assert await _listed_ids(world.client, token=world.token) == set()
    for session_id in (world.ft_session, world.loose_session):
        read = await tools_call(
            world.client, "get_session", {"session_id": session_id}, token=world.token
        )
        assert _outcome(read) == ("error", "not_found"), read.text


async def test_ac_111e_api_null_removes_the_scope(world) -> None:
    unscoped = await _set_scope(world.client, world.account["id"], None)

    assert unscoped.status_code == 200, unscoped.text
    assert unscoped.json()["project_ids"] is None
    assert agent_row(world.db_uri, world.account["id"]).project_ids is None
    assert world.pos_session in await _listed_ids(world.client, token=world.token)


async def test_ac_033a_denials_audit_account_route_and_reason_without_secrets(
    world, monkeypatch, caplog
) -> None:
    seen: list[dict] = []
    real = debug_logging.add_audit_attrs

    def spy(**attrs: object) -> None:
        seen.append(dict(attrs))
        real(**attrs)

    monkeypatch.setattr(debug_logging, "add_audit_attrs", spy)
    caplog.set_level("INFO", logger="omnigent.server.agent_project_scope")

    await tools_call(
        world.client, "get_session", {"session_id": world.pos_session}, token=world.token
    )
    await tools_call(world.client, "list_hosts", {"confirm": True}, token=world.token)

    denials = [row for row in seen if "scope_denied" in row]
    assert {row["scope_denied"] for row in denials} >= {"session_out_of_scope", "host_management"}
    for row in denials:
        assert row["account_id"] == world.account["id"]
        assert row["denied_route"]
    token_hash = agent_row(world.db_uri, world.account["id"]).token_hash
    recorded = json.dumps(seen, default=str) + caplog.text
    for secret in (world.token, token_hash, world.webhook_secret):
        assert secret not in recorded


async def test_ac_034a_account_read_list_and_update_hide_secrets(world) -> None:
    jens = headers_for(JENS)
    listed = await world.client.get("/v1/chat/agent-accounts", headers=jens)
    updated = await _set_scope(world.client, world.account["id"], [world.product_os])
    rotated = await world.client.post(
        f"/v1/chat/agent-accounts/{world.account['id']}/rotate", headers=jens
    )
    assert listed.status_code == updated.status_code == rotated.status_code == 200
    token_hash = agent_row(world.db_uri, world.account["id"]).token_hash
    account_views = [listed.json(), updated.json(), rotated.json()["account"]]

    serialized = json.dumps(account_views)
    for secret in (world.token, rotated.json()["token"], token_hash, world.webhook_secret):
        assert secret not in serialized
    for key in ("token", "token_hash", "webhook_secret"):
        assert f'"{key}"' not in serialized


def _failed_bot_reply(world: _World, session_id: str) -> str:
    """A bot reply with ``forward_state`` failed in an active thread on *session_id*."""
    channel = _session_channel(world.db_uri, session_id, JENS)
    root_id = uuid.uuid4().hex
    reply_id = uuid.uuid4().hex
    now = now_epoch_us()
    with Session(get_or_create_engine(world.db_uri)) as session:
        session.add(
            SqlChatMessage(
                id=root_id, channel_id=channel, author_id=JENS, created_at=now, body="root"
            )
        )
        session.add(
            SqlChatMessage(
                id=reply_id,
                channel_id=channel,
                thread_root_id=root_id,
                author_id=world.account["principal_id"],
                author_kind="agent_account",
                created_at=now + 1,
                body="bot reply",
                forward_state="failed",
            )
        )
        session.add(
            SqlChatThread(
                root_message_id=root_id,
                channel_id=channel,
                reply_count=1,
                participants="[]",
                bot_streak=0,
                agent_state="active",
                session_id=session_id,
                updated_at=now,
            )
        )
        session.commit()
    return reply_id


def _forward_state(db_uri: str, message_id: str) -> str | None:
    with Session(get_or_create_engine(db_uri)) as session:
        return session.scalar(
            select(SqlChatMessage.forward_state).where(SqlChatMessage.id == message_id)
        )


async def test_ac_112a_owner_retry_outside_allow_list_is_404(world) -> None:
    outside = _failed_bot_reply(world, world.pos_session)
    inside = _failed_bot_reply(world, world.ft_session)

    refused = await world.client.post(
        f"/v1/chat/messages/{outside}/forward", headers=headers_for(JENS)
    )
    control = await world.client.post(
        f"/v1/chat/messages/{inside}/forward", headers=headers_for(JENS)
    )

    assert refused.status_code == 404, refused.text
    assert _item_count(world.db_uri, world.pos_session) == 0
    assert _forward_state(world.db_uri, outside) == "failed"
    assert control.status_code != 404, control.text


async def test_ac_112b_scoped_mention_outside_allow_list_starts_no_session(world) -> None:
    channel = await world.client.post(
        "/v1/chat/channels", json={"name": "productos"}, headers=headers_for(JENS)
    )
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["id"]
    _set_project_name(world.db_uri, channel_id, PRODUCT_OS)
    SqlAlchemyChatStore(world.db_uri).add_member(channel_id, world.account["principal_id"])
    before = _conversation_count(world.db_uri)

    posted = await world.client.post(
        f"/v1/chat/channels/{channel_id}/messages",
        json={"body": f"{MENTION} start in ProductOS"},
        headers=headers_for(token=world.token),
    )
    assert posted.status_code == 201, posted.text
    root_id = posted.json()["id"]
    await _wait_for_start()

    thread = _thread(world.db_uri, root_id)
    assert not thread.session_id
    assert _conversation_count(world.db_uri) == before
    assert [code for _body, code in _system_rows(world.db_uri, root_id)] == ["forbidden"]
