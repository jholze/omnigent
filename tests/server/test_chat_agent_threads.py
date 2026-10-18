"""Agent threads opened when a person mentions the project agent."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from omnigent.db.db_models import (
    SqlChatChannel,
    SqlChatMember,
    SqlChatMessage,
    SqlChatThread,
    SqlChatUserPref,
    SqlConversation,
)
from omnigent.db.utils import get_or_create_engine, now_epoch_us
from omnigent.entities.conversation import MessageData, NewConversationItem
from omnigent.server.auth import LEVEL_OWNER, LEVEL_READ
from omnigent.server.chat_agent_threads import drain_pending, reconcile_due_starts
from omnigent.server.feature_flags import FEATURES_ENV_VAR
from omnigent.server.routes._sessions.helpers import _publish_status
from omnigent.server.routes.chat import create_chat_router
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.chat_store.sqlalchemy_store import SqlAlchemyChatStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from omnigent.stores.project_store.sqlalchemy_store import SqlAlchemyProjectStore
from tests.server.test_chat_routes import (
    ANNA,
    BOB,
    JENS,
    _build_app,
    _create_channel,
    _headers,
    _join,
    _open_dm,
    _post,
)

MENTION = "<@omnigent_agent:agent>"
NO_PROJECT = "Kein Projekt für Agenten-Start – Standard in Einstellungen setzen (no_project)"
HOST_OFFLINE = "Host offline (host_offline)"
AGENT_NOT_FOUND = "Agent nicht gefunden (agent_not_found)"
TIMEOUT = "Agent-Start hat das Zeitlimit überschritten (timeout)"
SHARING_OFF = "Live-Ansicht nur für Anna (Freigabe am Server deaktiviert)"
MIRROR_SUFFIX = "… vollständig in der Session"
LABEL_KEY = "omnigent.chat.v1"


@pytest_asyncio.fixture()
async def chat_client(
    db_uri: str,
    runtime_init: None,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    """Chat client with sharing left at its default."""
    monkeypatch.setenv(FEATURES_ENV_VAR, "team_chat")
    transport = httpx.ASGITransport(app=_build_app(db_uri, tmp_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture()
async def sharing_off_client(
    db_uri: str,
    runtime_init: None,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    """Chat client whose server rejects new session grants.

    The env default is what the app reads at boot. A sharing-mode file in the
    operator data dir would otherwise hide that default, so the test clears it.
    """
    monkeypatch.setattr(
        "omnigent.server.sharing_settings.read_sharing_mode_override",
        lambda: None,
    )
    monkeypatch.setenv(FEATURES_ENV_VAR, "team_chat")
    monkeypatch.setenv("OMNIGENT_SHARING_MODE", "off")
    transport = httpx.ASGITransport(app=_build_app(db_uri, tmp_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture()
async def runner_202(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """Accept the first session event without a live runner process."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(202)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://runner"
    ) as runner:

        async def resolve(*_args: object, **_kwargs: object) -> httpx.AsyncClient:
            return runner

        monkeypatch.setattr("omnigent.server.routes.sessions._get_runner_client", resolve)
        yield


@pytest_asyncio.fixture(autouse=True)
async def _stop_agent_threads() -> AsyncIterator[None]:
    """Cancel the in-process sweep and any start still waiting after the test."""
    yield
    from omnigent.server import chat_agent_threads as threads

    mirrors = list(threads._status_jobs)
    if mirrors:
        await asyncio.gather(*mirrors, return_exceptions=True)
    task = threads._timeout_task
    threads._timeout_task = None
    if task is not None and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    pending = [item for item in list(threads._pending) if not item.done()]
    for item in pending:
        item.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _session(db_uri: str) -> Session:
    return Session(get_or_create_engine(db_uri))


def _create_agent(db_uri: str) -> str:
    """Register a server agent whose bundle the policy loader can read."""
    from omnigent.runtime import get_artifact_store
    from tests.server.helpers import build_agent_bundle

    agent_id = uuid.uuid4().hex
    key = f"agents/{agent_id}/bundle.tar.gz"
    artifact_store = get_artifact_store()
    assert artifact_store is not None
    artifact_store.put(key, build_agent_bundle("coder"))
    SqlAlchemyAgentStore(db_uri).create(agent_id, "coder", key)
    return agent_id


def _create_project(db_uri: str, owner: str, name: str, config: dict | None = None) -> str:
    project_id = uuid.uuid4().hex
    SqlAlchemyProjectStore(db_uri).create(project_id, name, owner, config=config)
    return project_id


def _set_project_name(db_uri: str, channel_id: str, name: str) -> None:
    with _session(db_uri) as session:
        session.execute(
            update(SqlChatChannel).where(SqlChatChannel.id == channel_id).values(project_name=name)
        )
        session.commit()


def _set_default_project(db_uri: str, principal_id: str, project_id: str) -> None:
    with _session(db_uri) as session:
        session.add(
            SqlChatUserPref(
                principal_id=principal_id,
                default_project_id=project_id,
                updated_at=1,
            )
        )
        session.commit()


def _conversation_count(db_uri: str) -> int:
    with _session(db_uri) as session:
        return session.scalar(select(func.count()).select_from(SqlConversation)) or 0


def _thread(db_uri: str, root_id: str) -> SqlChatThread:
    with _session(db_uri) as session:
        row = session.execute(
            select(SqlChatThread).where(SqlChatThread.root_message_id == root_id)
        ).scalar_one()
        session.expunge(row)
        return row


def _system_rows(db_uri: str, root_id: str) -> list[tuple[str, str | None]]:
    with _session(db_uri) as session:
        rows = session.execute(
            select(SqlChatMessage.body, SqlChatMessage.system_code).where(
                SqlChatMessage.thread_root_id == root_id,
                SqlChatMessage.author_kind == "system",
            )
        ).all()
    return [(body, code) for body, code in rows]


def _user_text(db_uri: str, session_id: str) -> str:
    page = SqlAlchemyConversationStore(db_uri).list_items(session_id, limit=20, order="asc")
    for item in page.data:
        data = item.data
        if getattr(item, "type", None) != "message" or getattr(data, "role", None) != "user":
            continue
        return "\n".join(
            block["text"] for block in data.content if isinstance(block, dict) and "text" in block
        )
    raise AssertionError(f"session {session_id} has no user message")


async def _wait_for_start() -> None:
    await asyncio.wait_for(drain_pending(), timeout=45)


async def _thread_items(
    client: httpx.AsyncClient, user: str, channel_id: str, root_id: str
) -> list[dict]:
    response = await client.get(
        f"/v1/chat/channels/{channel_id}/messages",
        params={"thread_root_id": root_id, "limit": 200},
        headers=_headers(user),
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def _prepared_channel(
    client: httpx.AsyncClient, db_uri: str, *, project_name: str | None, config: dict | None
) -> tuple[str, str | None]:
    """Create a channel and, when asked, a project whose name the channel stores."""
    channel_id = await _create_channel(client, ANNA, "agents")
    project_id = None
    if project_name is not None:
        project_id = _create_project(db_uri, ANNA, project_name, config)
        _set_project_name(db_uri, channel_id, project_name)
    return channel_id, project_id


def _label(channel_id: str, root_id: str) -> str:
    return json.dumps({"channel_id": channel_id, "thread_root_id": root_id}, separators=(",", ":"))


def _assert_active_session(
    db_uri: str,
    *,
    channel_id: str,
    root_id: str,
    agent_id: str,
    project_id: str,
    before: int,
) -> str:
    assert _conversation_count(db_uri) == before + 1
    thread = _thread(db_uri, root_id)
    assert thread.agent_state == "active"
    assert thread.session_id
    conversation = SqlAlchemyConversationStore(db_uri).get_conversation(thread.session_id)
    assert conversation is not None
    assert conversation.id == thread.session_id
    assert conversation.agent_id == agent_id
    assert conversation.project_id == project_id
    assert conversation.host_id is None
    assert conversation.labels.get(LABEL_KEY) == _label(channel_id, root_id)
    assert (
        SqlAlchemyPermissionStore(db_uri).get_permission_level(ANNA, thread.session_id)
        == LEVEL_OWNER
    )
    return thread.session_id


async def test_ac_033a_channel_mention_starts_one_labeled_session(
    chat_client: httpx.AsyncClient, db_uri: str, runner_202: None
) -> None:
    agent_id = _create_agent(db_uri)
    channel_id, project_id = await _prepared_channel(
        chat_client,
        db_uri,
        project_name="billing",
        config={"agent_id": agent_id},
    )
    assert project_id is not None
    decoy_id = _create_project(db_uri, ANNA, "other", {"agent_id": agent_id})
    _set_default_project(db_uri, ANNA, decoy_id)
    before = _conversation_count(db_uri)

    posted = await _post(chat_client, ANNA, channel_id, f"{MENTION} schau mal")
    assert posted.status_code == 201, posted.text
    root_id = posted.json()["id"]
    await _wait_for_start()

    _assert_active_session(
        db_uri,
        channel_id=channel_id,
        root_id=root_id,
        agent_id=agent_id,
        project_id=project_id,
        before=before,
    )


async def test_ac_033a_default_project_starts_session_when_channel_has_no_project(
    chat_client: httpx.AsyncClient, db_uri: str, runner_202: None
) -> None:
    agent_id = _create_agent(db_uri)
    project_id = _create_project(db_uri, ANNA, "billing", {"agent_id": agent_id})
    _set_default_project(db_uri, ANNA, project_id)
    channel_id = await _create_channel(chat_client, ANNA, "agents")
    before = _conversation_count(db_uri)

    posted = await _post(chat_client, ANNA, channel_id, f"{MENTION} schau mal")
    assert posted.status_code == 201, posted.text
    await _wait_for_start()

    _assert_active_session(
        db_uri,
        channel_id=channel_id,
        root_id=posted.json()["id"],
        agent_id=agent_id,
        project_id=project_id,
        before=before,
    )


async def test_ac_033a_dm_mention_starts_session_from_default_project(
    chat_client: httpx.AsyncClient, db_uri: str, runner_202: None
) -> None:
    agent_id = _create_agent(db_uri)
    project_id = _create_project(db_uri, ANNA, "billing", {"agent_id": agent_id})
    _set_default_project(db_uri, ANNA, project_id)
    opened = await _open_dm(chat_client, ANNA, [JENS])
    assert opened.status_code in (200, 201), opened.text
    channel_id = opened.json()["id"]
    before = _conversation_count(db_uri)

    posted = await _post(chat_client, ANNA, channel_id, f"{MENTION} schau mal")
    assert posted.status_code == 201, posted.text
    await _wait_for_start()

    _assert_active_session(
        db_uri,
        channel_id=channel_id,
        root_id=posted.json()["id"],
        agent_id=agent_id,
        project_id=project_id,
        before=before,
    )


async def test_ac_033b_second_mention_does_not_create_another_session(
    chat_client: httpx.AsyncClient, db_uri: str, runner_202: None
) -> None:
    agent_id = _create_agent(db_uri)
    channel_id, project_id = await _prepared_channel(
        chat_client, db_uri, project_name="billing", config={"agent_id": agent_id}
    )
    assert project_id is not None
    before = _conversation_count(db_uri)

    first = await _post(chat_client, ANNA, channel_id, f"{MENTION} schau mal")
    assert first.status_code == 201, first.text
    root_id = first.json()["id"]
    second = await _post(
        chat_client,
        ANNA,
        channel_id,
        f"{MENTION} nochmal",
        thread_root_id=root_id,
    )
    assert second.status_code == 201, second.text
    await _wait_for_start()

    session_id = _assert_active_session(
        db_uri,
        channel_id=channel_id,
        root_id=root_id,
        agent_id=agent_id,
        project_id=project_id,
        before=before,
    )
    assert _thread(db_uri, root_id).session_id == session_id
    assert _conversation_count(db_uri) == before + 1


async def test_ac_034a_read_grant_for_other_human_members(
    chat_client: httpx.AsyncClient, db_uri: str, runner_202: None
) -> None:
    agent_id = _create_agent(db_uri)
    channel_id, _project_id = await _prepared_channel(
        chat_client, db_uri, project_name="billing", config={"agent_id": agent_id}
    )
    await _join(chat_client, JENS, channel_id)
    await _join(chat_client, BOB, channel_id)
    with _session(db_uri) as session:
        session.add(
            SqlChatMember(
                channel_id=channel_id,
                principal_id="agent:bot",
                role="member",
                joined_at=1,
            )
        )
        session.commit()

    posted = await _post(chat_client, ANNA, channel_id, f"{MENTION} schau mal")
    assert posted.status_code == 201, posted.text
    await _wait_for_start()

    session_id = _thread(db_uri, posted.json()["id"]).session_id
    assert session_id
    permissions = SqlAlchemyPermissionStore(db_uri)
    assert permissions.get_permission_level(ANNA, session_id) == LEVEL_OWNER
    assert permissions.get_permission_level(JENS, session_id) == LEVEL_READ
    assert permissions.get_permission_level(BOB, session_id) == LEVEL_READ
    assert permissions.get_permission_level("agent:bot", session_id) is None


async def test_ac_034b_sharing_off_skips_member_grants_and_posts_system_line(
    sharing_off_client: httpx.AsyncClient, db_uri: str, runner_202: None
) -> None:
    agent_id = _create_agent(db_uri)
    channel_id, _project_id = await _prepared_channel(
        sharing_off_client, db_uri, project_name="billing", config={"agent_id": agent_id}
    )
    await _join(sharing_off_client, JENS, channel_id)

    posted = await _post(sharing_off_client, ANNA, channel_id, f"{MENTION} schau mal")
    assert posted.status_code == 201, posted.text
    root_id = posted.json()["id"]
    await _wait_for_start()

    session_id = _thread(db_uri, root_id).session_id
    assert session_id
    assert _thread(db_uri, root_id).agent_state == "active"
    permissions = SqlAlchemyPermissionStore(db_uri)
    assert permissions.get_permission_level(ANNA, session_id) == LEVEL_OWNER
    assert permissions.get_permission_level(JENS, session_id) is None
    assert (SHARING_OFF, "sharing_disabled") in _system_rows(db_uri, root_id)
    items = await _thread_items(sharing_off_client, ANNA, channel_id, root_id)
    assert SHARING_OFF in [item["body"] for item in items]
    assert "Freigabe am Server deaktiviert" not in _user_text(db_uri, session_id)


async def test_ac_035a_no_project_posts_system_line_and_creates_no_session(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    channel_id = await _create_channel(chat_client, ANNA, "agents")
    before = _conversation_count(db_uri)

    posted = await _post(chat_client, ANNA, channel_id, f"{MENTION} schau mal")
    assert posted.status_code == 201, posted.text
    root_id = posted.json()["id"]
    await _wait_for_start()

    assert _conversation_count(db_uri) == before
    assert _thread(db_uri, root_id).agent_state == "start_failed"
    assert _thread(db_uri, root_id).session_id is None
    assert (NO_PROJECT, "no_project") in _system_rows(db_uri, root_id)
    items = await _thread_items(chat_client, ANNA, channel_id, root_id)
    assert NO_PROJECT in [item["body"] for item in items]


async def test_ac_035b_host_offline_posts_system_line_and_creates_no_session(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    agent_id = _create_agent(db_uri)
    channel_id, _project_id = await _prepared_channel(
        chat_client,
        db_uri,
        project_name="billing",
        config={"agent_id": agent_id, "host_id": uuid.uuid4().hex},
    )
    before = _conversation_count(db_uri)

    posted = await _post(chat_client, ANNA, channel_id, f"{MENTION} schau mal")
    assert posted.status_code == 201, posted.text
    root_id = posted.json()["id"]
    await _wait_for_start()

    assert _conversation_count(db_uri) == before
    assert _thread(db_uri, root_id).agent_state == "start_failed"
    assert _thread(db_uri, root_id).session_id is None
    assert (HOST_OFFLINE, "host_offline") in _system_rows(db_uri, root_id)
    items = await _thread_items(chat_client, ANNA, channel_id, root_id)
    assert HOST_OFFLINE in [item["body"] for item in items]


async def test_ac_037a_first_message_keeps_newest_context_within_char_cap(
    chat_client: httpx.AsyncClient, db_uri: str, runner_202: None
) -> None:
    agent_id = _create_agent(db_uri)
    channel_id, _project_id = await _prepared_channel(
        chat_client, db_uri, project_name="billing", config={"agent_id": agent_id}
    )

    def padded(token: str) -> str:
        return f"{token} " + ("x" * 860)

    root = await _post(chat_client, ANNA, channel_id, padded("msg-000"))
    assert root.status_code == 201, root.text
    root_id = root.json()["id"]
    for index in range(1, 25):
        reply = await _post(
            chat_client,
            ANNA,
            channel_id,
            padded(f"msg-{index:03d}"),
            thread_root_id=root_id,
        )
        assert reply.status_code == 201, reply.text
    trigger = await _post(
        chat_client,
        ANNA,
        channel_id,
        f"{MENTION} {padded('msg-trigger')}",
        thread_root_id=root_id,
    )
    assert trigger.status_code == 201, trigger.text
    await _wait_for_start()

    session_id = _thread(db_uri, root_id).session_id
    assert session_id
    text = _user_text(db_uri, session_id)
    assert len(text) <= 12_000
    assert "msg-000" not in text
    assert "msg-024" in text
    assert "msg-trigger" in text
    assert all(line.startswith("[Anna] ") for line in text.splitlines())


async def test_ac_038a_missing_agent_posts_agent_not_found_and_creates_no_session(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    _create_agent(db_uri)
    channel_id, _project_id = await _prepared_channel(
        chat_client, db_uri, project_name="billing", config={}
    )
    before = _conversation_count(db_uri)

    posted = await _post(chat_client, ANNA, channel_id, f"{MENTION} schau mal")
    assert posted.status_code == 201, posted.text
    root_id = posted.json()["id"]
    await _wait_for_start()

    assert _conversation_count(db_uri) == before
    assert _thread(db_uri, root_id).agent_state == "start_failed"
    assert _thread(db_uri, root_id).session_id is None
    assert (AGENT_NOT_FOUND, "agent_not_found") in _system_rows(db_uri, root_id)
    items = await _thread_items(chat_client, ANNA, channel_id, root_id)
    assert AGENT_NOT_FOUND in [item["body"] for item in items]


def _append_assistant(db_uri: str, session_id: str, text: str, response_id: str) -> None:
    SqlAlchemyConversationStore(db_uri).append(
        session_id,
        [
            NewConversationItem(
                type="message",
                response_id=response_id,
                data=MessageData(
                    role="assistant",
                    agent="coder",
                    content=[{"type": "output_text", "text": text}],
                ),
            )
        ],
    )


async def _wait_status_mirrors() -> None:
    """Wait until idle mirrors scheduled off the event loop have finished."""
    from omnigent.server import chat_agent_threads as threads

    pending = list(threads._status_jobs)
    if pending:
        await asyncio.wait_for(asyncio.gather(*pending), timeout=10)


def _agent_bodies(db_uri: str, root_id: str) -> list[str]:
    with _session(db_uri) as session:
        rows = session.execute(
            select(SqlChatMessage.body).where(
                SqlChatMessage.thread_root_id == root_id,
                SqlChatMessage.author_kind == "omnigent_agent",
                SqlChatMessage.author_id == "agent",
            )
        ).all()
    return [body for (body,) in rows]


async def test_ac_046a_idle_mirrors_assistant_text_once(
    chat_client: httpx.AsyncClient, db_uri: str, runner_202: None
) -> None:
    agent_id = _create_agent(db_uri)
    channel_id, _project_id = await _prepared_channel(
        chat_client, db_uri, project_name="billing", config={"agent_id": agent_id}
    )
    posted = await _post(chat_client, ANNA, channel_id, f"{MENTION} schau mal")
    assert posted.status_code == 201, posted.text
    root_id = posted.json()["id"]
    await _wait_for_start()
    session_id = _thread(db_uri, root_id).session_id
    assert session_id
    answer = "Tests grün, PR #42 offen"
    _append_assistant(db_uri, session_id, answer, "resp-046a")

    _publish_status(session_id, "idle", response_id="resp-046a")
    _publish_status(session_id, "idle", response_id="resp-046a")
    await _wait_status_mirrors()

    assert _agent_bodies(db_uri, root_id) == [answer]
    items = await _thread_items(chat_client, ANNA, channel_id, root_id)
    mirrored = [item for item in items if item["author_kind"] == "omnigent_agent"]
    assert [item["body"] for item in mirrored] == [answer]
    assert mirrored[0]["author_id"] == "agent"


async def test_ac_046b_idle_mirror_truncates_at_32000(
    chat_client: httpx.AsyncClient, db_uri: str, runner_202: None
) -> None:
    agent_id = _create_agent(db_uri)
    channel_id, _project_id = await _prepared_channel(
        chat_client, db_uri, project_name="billing", config={"agent_id": agent_id}
    )
    posted = await _post(chat_client, ANNA, channel_id, f"{MENTION} schau mal")
    assert posted.status_code == 201, posted.text
    root_id = posted.json()["id"]
    await _wait_for_start()
    session_id = _thread(db_uri, root_id).session_id
    assert session_id
    full = "A" * 50_000
    _append_assistant(db_uri, session_id, full, "resp-046b")

    _publish_status(session_id, "idle", response_id="resp-046b")
    await _wait_status_mirrors()

    (body,) = _agent_bodies(db_uri, root_id)
    assert body == ("A" * 32_000) + MIRROR_SUFFIX
    assert body != full


async def test_router_bind_mirrors_idle_without_a_chat_request(
    db_uri: str,
    runtime_init: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Building the chat router binds the store before any chat request.

    An idle for a linked agent thread then copies the assistant text. An
    idle for any other session does not read that summary.
    """
    from omnigent.server import chat_agent_threads as threads
    from omnigent.server import manager_webhook_context as webhook_context

    # runtime_init points the assistant summary at this database.
    assert runtime_init is None
    threads._bound_chat_store = None
    calls: list[str] = []
    real_build = webhook_context.build_lookups

    def wrapped(**kwargs: Any) -> tuple[Any, Any]:
        context_lookup, summary_lookup = real_build(**kwargs)

        def counting(lookup_id: str) -> str | None:
            calls.append(lookup_id)
            return summary_lookup(lookup_id)

        return context_lookup, counting

    monkeypatch.setattr(webhook_context, "build_lookups", wrapped)
    create_chat_router(SqlAlchemyChatStore(db_uri))

    unrelated = uuid.uuid4().hex
    _publish_status(unrelated, "idle", response_id="resp-unrelated")
    await _wait_status_mirrors()
    assert calls == []

    conversation = SqlAlchemyConversationStore(db_uri).create_conversation()
    session_id = conversation.id
    answer = "Nach Neustart gespiegelt"
    _append_assistant(db_uri, session_id, answer, "resp-restart")
    root_id = uuid.uuid4().hex
    channel_id = uuid.uuid4().hex
    with _session(db_uri) as session:
        session.add(
            SqlChatChannel(
                id=channel_id,
                kind="channel",
                visibility="public",
                created_at=now_epoch_us(),
            )
        )
        session.add(
            SqlChatThread(
                root_message_id=root_id,
                channel_id=channel_id,
                reply_count=0,
                participants="[]",
                bot_streak=0,
                agent_state="active",
                session_id=session_id,
                updated_at=now_epoch_us(),
            )
        )
        session.commit()

    _publish_status(session_id, "idle", response_id="resp-restart")
    await _wait_status_mirrors()

    assert calls == [session_id]
    assert _agent_bodies(db_uri, root_id) == [answer]


async def test_ac_087a_stale_starting_thread_fails_on_read_and_sweep(
    chat_client: httpx.AsyncClient, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    hold = asyncio.Event()

    async def _hang(*_args: object, **_kwargs: object) -> None:
        await hold.wait()

    monkeypatch.setattr("omnigent.server.chat_agent_threads._start_impl", _hang)
    channel_id = await _create_channel(chat_client, ANNA, "agents")
    posted = await _post(chat_client, ANNA, channel_id, f"{MENTION} schau mal")
    assert posted.status_code == 201, posted.text
    root_id = posted.json()["id"]
    with _session(db_uri) as session:
        session.execute(
            update(SqlChatThread)
            .where(SqlChatThread.root_message_id == root_id)
            .values(start_requested_at=now_epoch_us() - 121_000_000)
        )
        session.commit()

    items = await _thread_items(chat_client, ANNA, channel_id, root_id)

    assert TIMEOUT in [item["body"] for item in items]
    assert _thread(db_uri, root_id).agent_state == "start_failed"
    assert (TIMEOUT, "timeout") in _system_rows(db_uri, root_id)

    later_channel = await _create_channel(chat_client, ANNA, "later")
    later = await _post(chat_client, ANNA, later_channel, f"{MENTION} später")
    assert later.status_code == 201, later.text
    later_root = later.json()["id"]
    with _session(db_uri) as session:
        session.execute(
            update(SqlChatThread)
            .where(SqlChatThread.root_message_id == later_root)
            .values(start_requested_at=now_epoch_us() - 121_000_000)
        )
        session.commit()
    reconcile_due_starts(SqlAlchemyChatStore(db_uri))

    assert _thread(db_uri, later_root).agent_state == "start_failed"
    assert (TIMEOUT, "timeout") in _system_rows(db_uri, later_root)
