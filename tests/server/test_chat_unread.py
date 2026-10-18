"""Agent unread route and the human 403 (T-125, FR-088)."""

from __future__ import annotations

from tests.server.chat_testkit import (
    ANNA,
    JENS,
    add_member,
    channel_id,
    chat_client,
    create_account,
    headers_for,
    join,
    post_message,
)

_ = chat_client


async def test_ac_088c_agent_unread_then_read_then_human_403(chat_client, db_uri: str) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    token = created.json()["token"]
    principal = created.json()["account"]["principal_id"]
    billing = await channel_id(chat_client, JENS, "billing")
    await join(chat_client, ANNA, billing)
    add_member(db_uri, billing, principal)
    posted = await post_message(
        chat_client, ANNA, billing, f"<@agent_account:{principal}> prüf das"
    )
    assert posted.status_code == 201, posted.text
    message = posted.json()

    unread = await chat_client.get("/v1/chat/unread", headers=headers_for(token=token))
    assert unread.status_code == 200, unread.text
    items = unread.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["message_id"] == message["id"]
    assert item["channel_id"] == billing
    assert item["author"]["kind"] == "human"
    assert item["author"]["id"] == ANNA
    assert item["body"] == message["body"]
    assert item["created_at"] == message["created_at"]
    assert item["content_trust"] == "untrusted"

    marked = await chat_client.post(
        f"/v1/chat/channels/{billing}/read",
        json={"created_at": message["created_at"], "id": message["id"]},
        headers=headers_for(token=token),
    )
    assert marked.status_code == 204, marked.text
    again = await chat_client.get("/v1/chat/unread", headers=headers_for(token=token))
    assert again.json()["items"] == []

    human = await chat_client.get("/v1/chat/unread", headers=headers_for(ANNA))
    assert human.status_code == 403, human.text
