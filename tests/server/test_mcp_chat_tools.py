"""Chat tools over ``/mcp``: preview, post, unread, and mark-read (T-107)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from omnigent.db.db_models import SqlChatMessage
from omnigent.db.utils import get_or_create_engine
from tests.server.chat_testkit import (
    ANNA,
    JENS,
    add_member,
    channel_id,
    chat_client,
    create_account,
    join,
    rpc,
    tool_payload,
    tool_result,
    tools_call,
)

_ = chat_client
_ALLOWLIST = {
    "chat_list_channels",
    "chat_read",
    "chat_post",
    "chat_open_dm",
    "chat_list_members",
    "chat_unread",
    "chat_mark_read",
}


def _messages(db_uri: str) -> int:
    with Session(get_or_create_engine(db_uri)) as session:
        return int(session.scalar(select(func.count()).select_from(SqlChatMessage)) or 0)


async def test_ac_069b_chat_post_without_confirm_previews_and_stores_nothing(
    chat_client, db_uri: str
) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    token = created.json()["token"]
    billing = await channel_id(chat_client, JENS, "billing")
    add_member(db_uri, billing, created.json()["account"]["principal_id"])
    before = _messages(db_uri)
    preview = await tools_call(
        chat_client,
        "chat_post",
        {"channel": billing, "body": "Build läuft"},
        token=token,
    )
    result = tool_payload(preview)
    assert "preview" in result
    assert result["preview"]["method"] == "POST"
    assert result["preview"]["body"]["body"] == "Build läuft"
    assert _messages(db_uri) == before


async def test_ac_069a_user_chat_post_appears_in_the_channel(chat_client) -> None:
    general = await channel_id(chat_client, ANNA, "general")
    posted = await tools_call(
        chat_client,
        "chat_post",
        {"channel": general, "body": "Build läuft", "confirm": True},
        user=ANNA,
    )
    result = tool_payload(posted)
    assert result["deduplicated"] is False
    assert result["message"]["channel_id"] == general
    read = await tools_call(
        chat_client, "chat_read", {"channel": "#general", "tail": True}, user=ANNA
    )
    items = tool_payload(read)["items"]
    assert items[-1]["body"] == "Build läuft"
    assert items[-1]["author"]["kind"] == "human"
    assert items[-1]["content_trust"] == "untrusted"


async def test_ac_088a_mention_unread_then_mark_read_clears_it(chat_client, db_uri: str) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    token = created.json()["token"]
    principal = created.json()["account"]["principal_id"]
    billing = await channel_id(chat_client, JENS, "billing")
    await join(chat_client, ANNA, billing)
    add_member(db_uri, billing, principal)
    listed = await rpc(chat_client, "tools/list", token=token, protocol="2025-11-25")
    names = {tool["name"] for tool in tool_result(listed)["tools"]}
    owner = await rpc(chat_client, "tools/list", user=JENS, protocol="2025-11-25")
    assert names == {tool["name"] for tool in tool_result(owner)["tools"]}
    assert names >= _ALLOWLIST

    from tests.server.chat_testkit import post_message

    posted = await post_message(
        chat_client, ANNA, billing, f"<@agent_account:{principal}> fass zusammen"
    )
    assert posted.status_code == 201, posted.text
    unread = tool_payload(await tools_call(chat_client, "chat_unread", {}, token=token))
    assert len(unread["items"]) == 1
    item = unread["items"][0]
    assert item["channel_id"] == billing
    assert item["message_id"] == posted.json()["id"]
    assert item["author"]["kind"] == "human"
    assert item["content_trust"] == "untrusted"

    marked = tool_payload(
        await tools_call(
            chat_client,
            "chat_mark_read",
            {"channel": billing, "message_id": item["message_id"], "confirm": True},
            token=token,
        )
    )
    assert marked["cursor"]["id"] == item["message_id"]
    again = tool_payload(await tools_call(chat_client, "chat_unread", {}, token=token))
    assert again["items"] == []


async def test_ac_088b_owner_leave_drops_channel_from_chat_unread(
    chat_client, db_uri: str
) -> None:
    from omnigent.stores.chat_store.sqlalchemy_store import SqlAlchemyChatStore
    from tests.server.chat_testkit import headers_for, post_message

    created = await create_account(chat_client, JENS, "grok-jens")
    token = created.json()["token"]
    principal = created.json()["account"]["principal_id"]
    billing = await channel_id(chat_client, JENS, "billing")
    await join(chat_client, ANNA, billing)
    add_member(db_uri, billing, principal)
    posted = await post_message(
        chat_client, ANNA, billing, f"<@agent_account:{principal}> fass zusammen"
    )
    assert posted.status_code == 201, posted.text
    before = tool_payload(await tools_call(chat_client, "chat_unread", {}, token=token))
    assert [item["channel_id"] for item in before["items"]] == [billing]

    left = await chat_client.post(f"/v1/chat/channels/{billing}/leave", headers=headers_for(JENS))
    assert left.status_code == 204, left.text
    assert SqlAlchemyChatStore(db_uri).is_member(billing, principal)
    after = tool_payload(await tools_call(chat_client, "chat_unread", {}, token=token))
    assert all(item["channel_id"] != billing for item in after["items"])
