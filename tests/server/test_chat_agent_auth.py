"""``ogc_`` tokens authenticate only on ``/mcp`` and ``/v1/chat/*`` (T-105)."""

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
    rpc,
)

_ = chat_client


async def test_ac_064a_rotate_invalidates_the_previous_token_on_mcp(chat_client) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    assert created.status_code == 201, created.text
    account_id = created.json()["account"]["id"]
    old = created.json()["token"]
    rotated = await chat_client.post(
        f"/v1/chat/agent-accounts/{account_id}/rotate", headers=headers_for(JENS)
    )
    assert rotated.status_code == 200, rotated.text
    new = rotated.json()["token"]
    assert new.startswith("ogc_")
    assert new != old

    stale = await rpc(chat_client, "tools/list", token=old)
    assert stale.status_code == 401, stale.text
    fresh = await rpc(chat_client, "tools/list", token=new, protocol="2025-11-25")
    assert fresh.status_code == 200, fresh.text
    names = {tool["name"] for tool in fresh.json()["result"]["tools"]}
    owner = await rpc(chat_client, "tools/list", user=JENS, protocol="2025-11-25")
    owner_names = {tool["name"] for tool in owner.json()["result"]["tools"]}
    assert names == owner_names
    assert {
        "chat_list_channels",
        "chat_read",
        "chat_post",
        "chat_open_dm",
        "chat_list_members",
        "chat_unread",
        "chat_mark_read",
        "session_workspace",
        "host_fs",
    } <= names


async def test_ac_066a_agent_token_is_401_on_sessions(chat_client) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    token = created.json()["token"]
    sessions = await chat_client.get("/v1/sessions", headers=headers_for(token=token))
    assert sessions.status_code == 401, sessions.text


async def test_ac_066b_channels_are_the_owner_intersection(chat_client, db_uri: str) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    token = created.json()["token"]
    principal = created.json()["account"]["principal_id"]
    billing = await channel_id(chat_client, JENS, "billing")
    await join(chat_client, ANNA, billing)
    secret = await channel_id(chat_client, ANNA, "secret")
    add_member(db_uri, billing, principal)
    add_member(db_uri, secret, principal)

    listed = await chat_client.get("/v1/chat/channels", headers=headers_for(token=token))
    assert listed.status_code == 200, listed.text
    names = {row["name"] for row in listed.json()}
    assert "billing" in names
    assert "secret" not in names
    assert "general" not in names
