"""Bot streak brake and a bot mention that cannot start a session (T-109)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from omnigent.db.db_models import SqlChatMention, SqlChatThread
from omnigent.db.utils import get_or_create_engine
from tests.server.chat_testkit import (
    ANNA,
    JENS,
    add_member,
    channel_id,
    chat_client,
    create_account,
    headers_for,
    post_message,
)

_ = chat_client
_BRAKE = "Bots warten auf einen Menschen"
_NO_PROJECT = "Kein Projekt für Agenten-Start – Standard in Einstellungen setzen (no_project)"


def _count(db_uri: str, model: type) -> int:
    with Session(get_or_create_engine(db_uri)) as session:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)


async def _thread_messages(client, channel: str, root_id: str, *, user: str = JENS) -> list[dict]:
    resp = await client.get(
        f"/v1/chat/channels/{channel}/messages",
        params={"thread_root_id": root_id, "limit": 50},
        headers=headers_for(user),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


async def test_ac_074a_sixth_bot_post_is_409_and_the_notice_is_once(
    chat_client, db_uri: str
) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    principal = created.json()["account"]["principal_id"]
    token = created.json()["token"]
    billing = await channel_id(chat_client, JENS, "billing")
    add_member(db_uri, billing, principal)
    root = await post_message(chat_client, JENS, billing, "m1")
    assert root.status_code == 201, root.text
    root_id = root.json()["id"]
    for index in range(5):
        posted = await post_message(
            chat_client,
            principal,
            billing,
            f"bot {index}",
            token=token,
            thread_root_id=root_id,
        )
        assert posted.status_code == 201, posted.text
    before = _count(db_uri, SqlChatMention)
    rejected = await post_message(
        chat_client,
        principal,
        billing,
        f"<@user:{ANNA}> nochmal",
        token=token,
        thread_root_id=root_id,
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "bot_streak_limit"
    assert _count(db_uri, SqlChatMention) == before
    notices = [
        item
        for item in await _thread_messages(chat_client, billing, root_id)
        if item["body"] == _BRAKE
    ]
    assert len(notices) == 1
    assert notices[0]["author_kind"] == "system"
    again = await post_message(
        chat_client, principal, billing, "still waiting", token=token, thread_root_id=root_id
    )
    assert again.status_code == 409
    notices = [
        item
        for item in await _thread_messages(chat_client, billing, root_id)
        if item["body"] == _BRAKE
    ]
    assert len(notices) == 1


async def test_ac_075a_human_reply_resets_the_brake(chat_client, db_uri: str) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    principal = created.json()["account"]["principal_id"]
    token = created.json()["token"]
    billing = await channel_id(chat_client, JENS, "billing")
    add_member(db_uri, billing, principal)
    root = (await post_message(chat_client, JENS, billing, "m1")).json()
    for index in range(5):
        posted = await post_message(
            chat_client,
            principal,
            billing,
            f"bot {index}",
            token=token,
            thread_root_id=root["id"],
        )
        assert posted.status_code == 201, posted.text
    blocked = await post_message(
        chat_client, principal, billing, "nope", token=token, thread_root_id=root["id"]
    )
    assert blocked.status_code == 409
    human = await post_message(chat_client, JENS, billing, "Weiter", thread_root_id=root["id"])
    assert human.status_code == 201, human.text
    with Session(get_or_create_engine(db_uri)) as session:
        thread = session.get(SqlChatThread, (0, root["id"]))
        assert thread is not None
        assert thread.bot_streak == 0
        assert thread.brake_notice_at is None
    again = await post_message(
        chat_client, principal, billing, "again", token=token, thread_root_id=root["id"]
    )
    assert again.status_code == 201, again.text


async def test_ac_036b_agent_mention_without_a_project_fails_like_the_owner(
    chat_client, db_uri: str
) -> None:
    from omnigent.db.db_models import SqlConversation
    from omnigent.server.chat_agent_threads import drain_pending

    created = await create_account(chat_client, JENS, "grok-jens")
    principal = created.json()["account"]["principal_id"]
    token = created.json()["token"]
    general = await channel_id(chat_client, JENS, "general")
    add_member(db_uri, general, principal)
    before = _count(db_uri, SqlConversation)
    posted = await post_message(
        chat_client,
        principal,
        general,
        "<@omnigent_agent:coder> mach was",
        token=token,
    )
    assert posted.status_code == 201, posted.text
    await drain_pending()
    assert _count(db_uri, SqlConversation) == before
    root_id = posted.json()["id"]
    replies = await _thread_messages(chat_client, general, root_id)
    bodies = [item["body"] for item in replies]
    assert _NO_PROJECT in bodies
    assert "Nur Menschen können Omnigent-Agenten starten" not in bodies
    with Session(get_or_create_engine(db_uri)) as session:
        thread = session.get(SqlChatThread, (0, root_id))
        assert thread is not None
        assert thread.agent_state == "start_failed"
        assert thread.session_id is None
