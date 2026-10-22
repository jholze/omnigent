"""Thread replies follow an active agent thread into its session."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select

from omnigent.db.db_models import SqlChatMessage
from omnigent.server import chat_agent_threads
from omnigent.server.auth import LEVEL_EDIT, LEVEL_READ
from omnigent.server.routes.sessions import _STOP_SESSION_TYPE
from omnigent.stores.chat_store.sqlalchemy_store import SqlAlchemyChatStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from tests.server.test_chat_agent_threads import (  # noqa: F401 - pytest fixtures
    MENTION,
    _create_agent,
    _ensure_live_user,
    _prepared_channel,
    _session,
    _stop_agent_threads,
    _thread,
    _unwrap_chat_turn,
    _user_texts,
    _wait_for_start,
    chat_client,
    runner_202,
)
from tests.server.test_chat_routes import ANNA, JENS, _headers, _join, _post

BOT_SECRET = "test-chat-agent-secret"


@pytest_asyncio.fixture()
async def recording_runner(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[list[dict]]:
    """Accept runner calls and keep each JSON body, so a stop event would be seen."""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.content:
            bodies.append(json.loads(request.content))
        return httpx.Response(202)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://runner"
    ) as runner:

        async def resolve(*_args: object, **_kwargs: object) -> httpx.AsyncClient:
            return runner

        monkeypatch.setattr("omnigent.server.routes.sessions._get_runner_client", resolve)
        yield bodies


async def _active_thread(client: httpx.AsyncClient, db_uri: str) -> tuple[str, str, str]:
    """Anna opens an agent thread; Jens joins first, so he holds READ."""
    agent_id = _create_agent(db_uri)
    channel_id, _project_id = await _prepared_channel(
        client, db_uri, project_name="billing", config={"agent_id": agent_id}
    )
    await _join(client, JENS, channel_id)
    posted = await _post(client, ANNA, channel_id, f"{MENTION} schau mal")
    assert posted.status_code == 201, posted.text
    root_id = posted.json()["id"]
    await _wait_for_start()
    thread = _thread(db_uri, root_id)
    assert thread.agent_state == "active"
    assert thread.session_id
    assert (
        SqlAlchemyPermissionStore(db_uri).get_permission_level(JENS, thread.session_id)
        == LEVEL_READ
    )
    return channel_id, root_id, thread.session_id


def _grant_edit(db_uri: str, user: str, session_id: str) -> None:
    _ensure_live_user(db_uri, user)
    SqlAlchemyPermissionStore(db_uri).grant(user, session_id, LEVEL_EDIT)


def _forward_state(db_uri: str, message_id: str) -> str | None:
    with _session(db_uri) as session:
        return session.execute(
            select(SqlChatMessage.forward_state).where(SqlChatMessage.id == message_id)
        ).scalar_one()


def _rows_with_body(db_uri: str, root_id: str, body: str) -> int:
    with _session(db_uri) as session:
        return (
            session.scalar(
                select(func.count())
                .select_from(SqlChatMessage)
                .where(SqlChatMessage.thread_root_id == root_id, SqlChatMessage.body == body)
            )
            or 0
        )


async def _reply(
    client: httpx.AsyncClient, user: str, channel_id: str, root_id: str, body: str
) -> httpx.Response:
    response = await _post(client, user, channel_id, body, thread_root_id=root_id)
    await _wait_for_start()
    return response


async def _dismissed_thread(client: httpx.AsyncClient, db_uri: str) -> tuple[str, str, str]:
    channel_id, root_id, session_id = await _active_thread(client, db_uri)
    dismissed = await client.post(f"/v1/chat/threads/{root_id}/dismiss", headers=_headers(ANNA))
    assert dismissed.status_code == 200, dismissed.text
    assert _thread(db_uri, root_id).agent_state == "dismissed"
    return channel_id, root_id, session_id


async def _bot_reply(
    client: httpx.AsyncClient,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    owner: str,
    channel_id: str,
    root_id: str,
    body: str,
) -> httpx.Response:
    """*owner* creates the Grok account, adds it to the channel, and it replies."""
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", BOT_SECRET)
    created = await client.post(
        "/v1/chat/agent-accounts",
        json={
            "display_name": "Grok",
            "handle": f"grok-{owner.split('@', 1)[0]}",
            "webhook_url": "https://bot.example.com/hook",
        },
        headers=_headers(owner),
    )
    assert created.status_code == 201, created.text
    _ensure_live_user(db_uri, owner)
    SqlAlchemyChatStore(db_uri).add_member(channel_id, created.json()["account"]["principal_id"])
    response = await client.post(
        f"/v1/chat/channels/{channel_id}/messages",
        json={"body": body, "thread_root_id": root_id},
        headers={"authorization": f"Bearer {created.json()['token']}"},
    )
    await _wait_for_start()
    return response


async def test_ac_040a_edit_human_plain_reply_is_forwarded_with_name(
    chat_client: httpx.AsyncClient,  # noqa: F811  (imported fixture)
    db_uri: str,
    runner_202: None,  # noqa: F811  (imported fixture)
) -> None:
    channel_id, root_id, session_id = await _active_thread(chat_client, db_uri)
    _grant_edit(db_uri, JENS, session_id)

    reply = await _reply(chat_client, JENS, channel_id, root_id, "bitte auch die Tests")
    assert reply.status_code == 201, reply.text

    turns = _user_texts(db_uri, session_id)
    assert len(turns) == 2
    assert _unwrap_chat_turn(turns[1]) == "[Jens] bitte auch die Tests"
    assert _forward_state(db_uri, reply.json()["id"]) == "forwarded"


async def test_ac_041a_read_author_reply_is_stored_not_forwarded(
    chat_client: httpx.AsyncClient,  # noqa: F811  (imported fixture)
    db_uri: str,
    runner_202: None,  # noqa: F811  (imported fixture)
) -> None:
    channel_id, root_id, session_id = await _active_thread(chat_client, db_uri)

    reply = await _reply(chat_client, JENS, channel_id, root_id, "nur lesend")
    assert reply.status_code == 201, reply.text

    assert _rows_with_body(db_uri, root_id, "nur lesend") == 1
    assert len(_user_texts(db_uri, session_id)) == 1
    assert _forward_state(db_uri, reply.json()["id"]) == "not_forwarded"


async def test_ac_042a_owner_dismiss_stops_forwarding_but_not_the_session(
    chat_client: httpx.AsyncClient,  # noqa: F811  (imported fixture)
    db_uri: str,
    recording_runner: list[dict],
) -> None:
    channel_id, root_id, session_id = await _active_thread(chat_client, db_uri)

    dismissed = await _reply(chat_client, ANNA, channel_id, root_id, "/dismiss")
    assert dismissed.status_code == 201, dismissed.text
    assert _thread(db_uri, root_id).agent_state == "dismissed"

    after = await _reply(chat_client, ANNA, channel_id, root_id, "noch da?")
    assert after.status_code == 201, after.text
    assert len(_user_texts(db_uri, session_id)) == 1
    assert _forward_state(db_uri, after.json()["id"]) != "forwarded"

    assert recording_runner
    assert not [body for body in recording_runner if body.get("type") == _STOP_SESSION_TYPE]
    conversation = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert conversation is not None
    assert not conversation.archived
    assert _thread(db_uri, root_id).session_id == session_id


async def test_ac_042b_read_user_cannot_dismiss_by_command_or_route(
    chat_client: httpx.AsyncClient,  # noqa: F811  (imported fixture)
    db_uri: str,
    runner_202: None,  # noqa: F811  (imported fixture)
) -> None:
    channel_id, root_id, _session_id = await _active_thread(chat_client, db_uri)

    command = await _reply(chat_client, JENS, channel_id, root_id, "/dismiss")
    assert command.status_code == 403, command.text
    assert _rows_with_body(db_uri, root_id, "/dismiss") == 0
    assert _thread(db_uri, root_id).agent_state == "active"

    route = await chat_client.post(f"/v1/chat/threads/{root_id}/dismiss", headers=_headers(JENS))
    assert route.status_code == 403, route.text
    assert _thread(db_uri, root_id).agent_state == "active"


async def test_ac_043a_edit_human_mention_reactivates_dismissed_thread(
    chat_client: httpx.AsyncClient,  # noqa: F811  (imported fixture)
    db_uri: str,
    runner_202: None,  # noqa: F811  (imported fixture)
) -> None:
    channel_id, root_id, session_id = await _dismissed_thread(chat_client, db_uri)
    _grant_edit(db_uri, JENS, session_id)
    body = f"{MENTION} weiter bitte"

    mention = await _reply(chat_client, JENS, channel_id, root_id, body)
    assert mention.status_code == 201, mention.text

    thread = _thread(db_uri, root_id)
    assert thread.agent_state == "active"
    assert thread.session_id == session_id
    turns = _user_texts(db_uri, session_id)
    assert len(turns) == 2
    assert _unwrap_chat_turn(turns[1]) == f"[Jens] {body}"


async def test_ac_043b_read_user_mention_keeps_thread_dismissed(
    chat_client: httpx.AsyncClient,  # noqa: F811  (imported fixture)
    db_uri: str,
    runner_202: None,  # noqa: F811  (imported fixture)
) -> None:
    channel_id, root_id, session_id = await _dismissed_thread(chat_client, db_uri)

    mention = await _reply(chat_client, JENS, channel_id, root_id, f"{MENTION} weiter")
    assert mention.status_code == 201, mention.text

    assert _thread(db_uri, root_id).agent_state == "dismissed"
    assert len(_user_texts(db_uri, session_id)) == 1
    assert _forward_state(db_uri, mention.json()["id"]) == "not_forwarded"


async def test_ac_054a_failed_forward_is_retried_once(
    chat_client: httpx.AsyncClient,  # noqa: F811  (imported fixture)
    db_uri: str,
    runner_202: None,  # noqa: F811  (imported fixture)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel_id, root_id, session_id = await _active_thread(chat_client, db_uri)
    real_post = chat_agent_threads._post_first_message

    async def broken(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("session post failed")

    monkeypatch.setattr(chat_agent_threads, "_post_first_message", broken)
    reply = await _reply(chat_client, ANNA, channel_id, root_id, "zweiter Versuch")
    assert reply.status_code == 201, reply.text
    message_id = reply.json()["id"]
    assert _forward_state(db_uri, message_id) == "failed"
    assert len(_user_texts(db_uri, session_id)) == 1

    monkeypatch.setattr(chat_agent_threads, "_post_first_message", real_post)
    first = await chat_client.post(
        f"/v1/chat/messages/{message_id}/forward", headers=_headers(ANNA)
    )
    assert first.status_code == 200, first.text
    assert first.json() == {"id": message_id, "forward_state": "forwarded"}
    second = await chat_client.post(
        f"/v1/chat/messages/{message_id}/forward", headers=_headers(ANNA)
    )
    assert second.status_code == 200, second.text
    assert second.json()["forward_state"] == "forwarded"

    turns = _user_texts(db_uri, session_id)
    assert len(turns) == 2
    assert _unwrap_chat_turn(turns[1]) == "[Anna] zweiter Versuch"
    assert _forward_state(db_uri, message_id) == "forwarded"


async def test_ac_077a_bot_reply_forwards_with_bot_and_owner_name(
    chat_client: httpx.AsyncClient,  # noqa: F811  (imported fixture)
    db_uri: str,
    runner_202: None,  # noqa: F811  (imported fixture)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel_id, root_id, session_id = await _active_thread(chat_client, db_uri)
    _grant_edit(db_uri, JENS, session_id)

    reply = await _bot_reply(
        chat_client,
        db_uri,
        monkeypatch,
        owner=JENS,
        channel_id=channel_id,
        root_id=root_id,
        body="Grok meint: passt",
    )
    assert reply.status_code == 201, reply.text

    turns = _user_texts(db_uri, session_id)
    assert len(turns) == 2
    assert _unwrap_chat_turn(turns[1]) == "[Grok (Jens)] Grok meint: passt"
    assert _forward_state(db_uri, reply.json()["id"]) == "forwarded"


async def test_ac_077b_bot_reply_with_read_owner_is_not_forwarded(
    chat_client: httpx.AsyncClient,  # noqa: F811  (imported fixture)
    db_uri: str,
    runner_202: None,  # noqa: F811  (imported fixture)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel_id, root_id, session_id = await _active_thread(chat_client, db_uri)

    reply = await _bot_reply(
        chat_client,
        db_uri,
        monkeypatch,
        owner=JENS,
        channel_id=channel_id,
        root_id=root_id,
        body="Grok nur lesend",
    )
    assert reply.status_code == 201, reply.text

    assert _rows_with_body(db_uri, root_id, "Grok nur lesend") == 1
    assert len(_user_texts(db_uri, session_id)) == 1
    assert _forward_state(db_uri, reply.json()["id"]) == "not_forwarded"
