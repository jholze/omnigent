"""Session chat beside a shared root session, served from the team-chat store.

``GET``/``POST /v1/sessions/{id}/chat`` are adapters over the ``kind=session``
channel of the root. Membership comes from the root's direct grants. The
``team_chat`` flag is on through the ``chat_client`` fixture.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from omnigent.runtime import session_stream
from omnigent.server.auth import LEVEL_EDIT, LEVEL_OWNER, LEVEL_READ, RESERVED_USER_PUBLIC
from omnigent.stores.chat_store.sqlalchemy_store import SqlAlchemyChatStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from tests.server.chat_testkit import ANNA, BOB, CARLA, JENS, chat_client, headers_for

_ = chat_client

ADMIN = "admin@bloopark.de"
AGENT_THREAD_LABEL = "omnigent.chat.v1"


def _root_session(db_uri: str, owner: str, labels: dict[str, str] | None = None) -> str:
    """A root session with a direct owner grant."""
    conversation = SqlAlchemyConversationStore(db_uri).create_conversation(
        title="shared", labels=labels
    )
    _grant(db_uri, owner, conversation.id, LEVEL_OWNER)
    return conversation.id


def _grant(db_uri: str, user_id: str, session_id: str, level: int) -> None:
    permissions = SqlAlchemyPermissionStore(db_uri)
    if user_id != RESERVED_USER_PUBLIC:
        permissions.ensure_user(user_id)
    permissions.grant(user_id, session_id, level)


async def _get(client: httpx.AsyncClient, session_id: str, user: str) -> httpx.Response:
    return await client.get(f"/v1/sessions/{session_id}/chat", headers=headers_for(user))


async def _post(
    client: httpx.AsyncClient, session_id: str, user: str, body: str
) -> httpx.Response:
    return await client.post(
        f"/v1/sessions/{session_id}/chat", json={"body": body}, headers=headers_for(user)
    )


async def test_ac_029a_two_direct_grants_list_the_session_channel(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    session_id = _root_session(db_uri, JENS)
    _grant(db_uri, ANNA, session_id, LEVEL_EDIT)

    posted = await _post(chat_client, session_id, ANNA, "Schau dir den Test an")
    assert posted.status_code == 201, posted.text

    channel = SqlAlchemyChatStore(db_uri).get_session_channel(session_id)
    assert channel is not None
    assert channel.kind == "session"
    listed = await _get(chat_client, session_id, JENS)
    assert listed.status_code == 200, listed.text
    assert [(row["id"], row["created_by"], row["body"]) for row in listed.json()["items"]] == [
        (posted.json()["id"], ANNA, "Schau dir den Test an")
    ]


async def test_ac_029b_public_link_caller_gets_403(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    session_id = _root_session(db_uri, JENS)
    _grant(db_uri, RESERVED_USER_PUBLIC, session_id, LEVEL_READ)

    response = await _get(chat_client, session_id, CARLA)

    assert response.status_code == 403, response.text


async def test_ac_029c_agent_thread_root_has_no_session_chat(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    session_id = _root_session(db_uri, ANNA, labels={AGENT_THREAD_LABEL: "thread"})
    _grant(db_uri, JENS, session_id, LEVEL_EDIT)
    _grant(db_uri, BOB, session_id, LEVEL_EDIT)

    listed = await _get(chat_client, session_id, ANNA)
    posted = await _post(chat_client, session_id, ANNA, "hallo")

    assert listed.status_code == 404, listed.text
    assert posted.status_code == 404, posted.text
    assert SqlAlchemyChatStore(db_uri).get_session_channel(session_id) is None


async def test_ac_030a_post_creates_no_conversation_item_or_agent_event(
    chat_client: httpx.AsyncClient, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = _root_session(db_uri, JENS)
    _grant(db_uri, ANNA, session_id, LEVEL_EDIT)
    conversations = SqlAlchemyConversationStore(db_uri)
    items_before = [item.id for item in conversations.list_items(session_id, limit=100).data]
    published: list[dict[str, Any]] = []
    real_publish = session_stream.publish

    def _record(conversation_id: str, event: dict[str, Any]) -> int:
        published.append(event)
        return real_publish(conversation_id, event)

    monkeypatch.setattr(session_stream, "publish", _record)

    posted = await _post(chat_client, session_id, ANNA, "Nur für Menschen")

    assert posted.status_code == 201, posted.text
    items_after = [item.id for item in conversations.list_items(session_id, limit=100).data]
    assert items_after == items_before
    assert [event["type"] for event in published] == ["session.human_chat"]


async def test_ac_031b_get_keeps_the_previous_response_shape(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    session_id = _root_session(db_uri, JENS)
    _grant(db_uri, ANNA, session_id, LEVEL_EDIT)
    posted = await _post(chat_client, session_id, JENS, "I'll look at the test")
    assert posted.status_code == 201, posted.text

    listed = await _get(chat_client, session_id, JENS)

    assert listed.status_code == 200, listed.text
    payload = listed.json()
    assert set(payload) == {"items", "shared"}
    assert payload["shared"] is True
    (item,) = payload["items"]
    assert set(item) == {"id", "created_by", "created_at", "body"}
    assert item == posted.json()
    assert isinstance(item["created_at"], int)


async def test_ac_029_admin_reads_but_cannot_post(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    session_id = _root_session(db_uri, JENS)
    _grant(db_uri, ANNA, session_id, LEVEL_EDIT)
    SqlAlchemyPermissionStore(db_uri).ensure_user(ADMIN, is_admin=True)

    listed = await _get(chat_client, session_id, ADMIN)
    posted = await _post(chat_client, session_id, ADMIN, "from admin")

    assert listed.status_code == 200, listed.text
    assert posted.status_code == 403, posted.text


async def test_ac_029_stranger_gets_404(chat_client: httpx.AsyncClient, db_uri: str) -> None:
    session_id = _root_session(db_uri, JENS)
    _grant(db_uri, ANNA, session_id, LEVEL_EDIT)

    listed = await _get(chat_client, session_id, CARLA)
    posted = await _post(chat_client, session_id, CARLA, "no")

    assert listed.status_code == 404, listed.text
    assert posted.status_code == 404, posted.text


async def test_ac_029_second_direct_grant_makes_shared_true(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    session_id = _root_session(db_uri, JENS)
    _grant(db_uri, RESERVED_USER_PUBLIC, session_id, LEVEL_READ)

    alone = await _get(chat_client, session_id, JENS)
    _grant(db_uri, ANNA, session_id, LEVEL_EDIT)
    with_anna = await _get(chat_client, session_id, JENS)

    assert alone.status_code == 200, alone.text
    assert alone.json()["shared"] is False
    assert with_anna.json()["shared"] is True
