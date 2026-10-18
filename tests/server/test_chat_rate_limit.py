"""Post cap and the combined agent-account window (T-119, FR-017, FR-092)."""

from __future__ import annotations

import pytest

from omnigent.server.chat_rate_limit import reset_chat_rate_limits
from tests.server.chat_testkit import (
    ANNA,
    JENS,
    add_member,
    channel_id,
    chat_client,
    create_account,
    headers_for,
    post_message,
    rpc,
    tools_call,
)

_ = chat_client


@pytest.fixture(autouse=True)
def _reset_windows():
    reset_chat_rate_limits()
    yield
    reset_chat_rate_limits()


async def test_ac_017a_and_ac_017b_post_cap_is_per_channel(chat_client, monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_AGENT_CHAT_POST_RPM", "2")
    general = await channel_id(chat_client, ANNA, "general")
    billing = await channel_id(chat_client, ANNA, "billing")
    assert (await post_message(chat_client, ANNA, general, "one")).status_code == 201
    assert (await post_message(chat_client, ANNA, general, "two")).status_code == 201
    blocked = await post_message(chat_client, ANNA, general, "three")
    assert blocked.status_code == 429, blocked.text
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert blocked.headers["retry-after"]
    retry = int(blocked.headers["retry-after"])
    assert 1 <= retry <= 60
    other = await post_message(chat_client, ANNA, billing, "still ok")
    assert other.status_code == 201, other.text


async def test_ac_092a_tools_call_over_the_limit_is_a_tool_error(
    chat_client, db_uri: str, monkeypatch
) -> None:
    monkeypatch.setenv("OMNIGENT_AGENT_MCP_RPM", "1")
    created = await create_account(chat_client, JENS, "grok-jens")
    token = created.json()["token"]
    billing = await channel_id(chat_client, JENS, "billing")
    add_member(db_uri, billing, created.json()["account"]["principal_id"])
    assert (await rpc(chat_client, "tools/list", token=token)).status_code == 200
    called = await tools_call(
        chat_client,
        "chat_post",
        {"channel": billing, "body": "over", "confirm": True},
        token=token,
        protocol="2025-06-18",
    )
    assert called.status_code == 200, called.text
    assert called.headers["retry-after"]
    retry = int(called.headers["retry-after"])
    assert 1 <= retry <= 60
    result = called.json()["result"]
    assert "error" not in called.json()
    assert result["isError"] is True
    assert result["content"][0]["text"].startswith("rate_limited: ")
    error = result["structuredContent"]["error"]
    assert error["code"] == "rate_limited"
    assert 1 <= error["retry_after_s"] <= 60
    read = await chat_client.get(
        f"/v1/chat/channels/{billing}/messages", headers=headers_for(JENS)
    )
    assert all(item["body"] != "over" for item in read.json()["items"])


async def test_ac_092b_other_mcp_methods_and_chat_routes_are_http_429(
    chat_client, monkeypatch
) -> None:
    monkeypatch.setenv("OMNIGENT_AGENT_MCP_RPM", "1")
    created = await create_account(chat_client, JENS, "grok-jens")
    token = created.json()["token"]
    assert (await rpc(chat_client, "ping", token=token)).status_code == 200
    listed = await rpc(chat_client, "tools/list", token=token)
    assert listed.status_code == 429, listed.text
    assert listed.headers["retry-after"]
    assert listed.json()["error"]["code"] == -32000
    assert listed.json()["error"]["data"]["code"] == "rate_limited"
    channels = await chat_client.get("/v1/chat/channels", headers=headers_for(token=token))
    assert channels.status_code == 429, channels.text
    assert channels.json()["error"]["code"] == "rate_limited"
    assert channels.headers["retry-after"]


async def test_ac_092c_inprocess_chat_reads_are_not_double_counted(
    chat_client, db_uri: str, monkeypatch
) -> None:
    """Production window is 120. A limit of 4 fails on the 3rd read if counted twice."""
    monkeypatch.setenv("OMNIGENT_AGENT_MCP_RPM", "4")
    created = await create_account(chat_client, JENS, "grok-jens")
    token = created.json()["token"]
    billing = await channel_id(chat_client, JENS, "billing")
    add_member(db_uri, billing, created.json()["account"]["principal_id"])
    for index in range(4):
        called = await tools_call(
            chat_client, "chat_read", {"channel": billing, "limit": 1}, token=token
        )
        assert called.status_code == 200, called.text
        assert called.json()["result"].get("isError") is not True, index
    blocked = await tools_call(
        chat_client, "chat_read", {"channel": billing, "limit": 1}, token=token
    )
    assert blocked.json()["result"]["content"][0]["text"].startswith("rate_limited: ")
    human = await chat_client.get("/v1/chat/bootstrap", headers=headers_for(ANNA))
    assert human.status_code == 200, human.text
