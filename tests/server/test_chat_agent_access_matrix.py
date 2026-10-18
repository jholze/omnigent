"""Agent-account rights: the bot stays inside its owner's membership (T-106)."""

from __future__ import annotations

from tests.server.chat_testkit import (
    ANNA,
    JENS,
    add_member,
    channel_id,
    chat_client,
    create_account,
    headers_for,
    post_message,
    seed_session_channel,
    tools_call,
)

_ = chat_client


async def _bot(client, db_uri: str, channel: str) -> tuple[str, str]:
    created = await create_account(client, JENS, "grok-jens")
    assert created.status_code == 201, created.text
    principal = created.json()["account"]["principal_id"]
    token = created.json()["token"]
    add_member(db_uri, channel, principal)
    return principal, token


async def test_ac_021c_dm_without_owner_is_agent_owner_required(chat_client) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    principal = created.json()["account"]["principal_id"]
    opened = await chat_client.post(
        "/v1/chat/dms",
        json={"principals": [principal]},
        headers=headers_for(ANNA),
    )
    assert opened.status_code == 403, opened.text
    assert opened.json()["error"]["code"] == "agent_owner_required"


async def test_ac_067a_owner_leave_makes_the_next_read_403(chat_client, db_uri: str) -> None:
    billing = await channel_id(chat_client, JENS, "billing")
    _principal, token = await _bot(chat_client, db_uri, billing)
    left = await chat_client.post(f"/v1/chat/channels/{billing}/leave", headers=headers_for(JENS))
    assert left.status_code == 204, left.text
    read = await tools_call(
        chat_client, "chat_read", {"channel": billing, "limit": 10}, token=token
    )
    assert read.status_code == 200, read.text
    result = read.json()["result"]
    assert "error" not in read.json()
    assert result["isError"] is True
    assert result["content"][0]["text"].startswith("not_a_member: ")
    assert result["structuredContent"]["error"]["http_status"] == 403


async def test_ac_068c_direct_elicitation_resolve_stays_401(chat_client, db_uri: str) -> None:
    billing = await channel_id(chat_client, JENS, "billing")
    _principal, token = await _bot(chat_client, db_uri, billing)
    resolve = await chat_client.post(
        "/v1/sessions/S1/elicitations/e1/resolve",
        json={"action": "accept"},
        headers=headers_for(token=token),
    )
    assert resolve.status_code == 401, resolve.text


async def test_ac_068d_agent_cannot_manage_agent_accounts(chat_client, db_uri: str) -> None:
    billing = await channel_id(chat_client, JENS, "billing")
    _principal, token = await _bot(chat_client, db_uri, billing)
    created = await chat_client.post(
        "/v1/chat/agent-accounts",
        json={
            "display_name": "Other",
            "handle": "other-bot",
            "webhook_url": "https://bot.example.com/hook",
        },
        headers=headers_for(token=token),
    )
    assert created.status_code == 403, created.text
    listed = await chat_client.get("/v1/chat/channels", headers=headers_for(token=token))
    assert listed.status_code == 200, listed.text


async def test_ac_068e_agent_can_create_a_channel_with_its_owner(
    chat_client, db_uri: str, monkeypatch
) -> None:
    import omnigent.debug_logging as debug_logging

    created_account = await create_account(chat_client, JENS, "grok-jens")
    account = created_account.json()["account"]
    token = created_account.json()["token"]
    seen: list[dict] = []
    real = debug_logging.add_audit_attrs

    def spy(**attrs: object) -> None:
        seen.append(dict(attrs))
        real(**attrs)

    monkeypatch.setattr(debug_logging, "add_audit_attrs", spy)
    created = await chat_client.post(
        "/v1/chat/channels",
        json={"name": "bot-lab"},
        headers=headers_for(token=token),
    )
    assert created.status_code == 201, created.text
    channel = created.json()["id"]
    members = await chat_client.get(
        f"/v1/chat/channels/{channel}/members", headers=headers_for(JENS)
    )
    assert members.status_code == 200, members.text
    ids = {row["principal_id"] for row in members.json()}
    assert account["principal_id"] in ids
    assert JENS in ids
    merged = {key: value for row in seen for key, value in row.items()}
    assert merged.get("account_id") == account["id"]
    assert merged.get("owner_id") == JENS


async def test_agent_session_chat_follows_the_owner(chat_client, db_uri: str) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    principal = created.json()["account"]["principal_id"]
    token = created.json()["token"]
    channel = seed_session_channel(db_uri, JENS)
    listed = await chat_client.get(
        f"/v1/chat/channels/{channel}/messages", headers=headers_for(token=token)
    )
    assert listed.status_code == 200, listed.text
    hidden = seed_session_channel(db_uri, principal)
    denied = await chat_client.get(
        f"/v1/chat/channels/{hidden}/messages", headers=headers_for(token=token)
    )
    assert denied.status_code == 404, denied.text


async def test_agent_join_requires_the_owner(chat_client, db_uri: str) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    principal = created.json()["account"]["principal_id"]
    secret = await channel_id(chat_client, ANNA, "secret")
    denied = await chat_client.post(
        f"/v1/chat/channels/{secret}/join",
        headers={"X-Forwarded-Email": principal},
    )
    assert denied.status_code == 403, denied.text
    billing = await channel_id(chat_client, JENS, "billing")
    allowed = await chat_client.post(
        f"/v1/chat/channels/{billing}/join",
        headers={"X-Forwarded-Email": principal},
    )
    assert allowed.status_code == 204, allowed.text
    posted = await post_message(chat_client, principal, billing, "from the bot")
    assert posted.status_code == 201, posted.text
