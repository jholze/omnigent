"""MCP audit attributes and last_seen throttling (T-131)."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from omnigent.db.db_models import SqlChatAgentAccount
from omnigent.db.utils import get_or_create_engine, now_epoch_us
from omnigent.server.auth import LEVEL_OWNER
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from tests.server.chat_testkit import (
    JENS,
    add_member,
    agent_row,
    channel_id,
    chat_client,
    create_account,
    headers_for,
    post_message,
    rpc,
    tools_call,
)

_ = chat_client
_MARKER = "MARKER-7f3a"


def _seen(db_uri: str, account_id: str) -> int | None:
    with Session(get_or_create_engine(db_uri)) as session:
        row = session.get(SqlChatAgentAccount, (0, account_id))
        assert row is not None
        return row.last_seen_at


async def test_ac_100a_audit_records_the_call_and_not_the_body(
    chat_client, db_uri: str, monkeypatch, caplog
) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    account = created.json()["account"]
    token = created.json()["token"]
    billing = await channel_id(chat_client, JENS, "billing")
    add_member(db_uri, billing, account["principal_id"])
    seen: list[dict] = []
    real = __import__("omnigent.debug_logging", fromlist=["add_audit_attrs"]).add_audit_attrs

    def spy(**attrs: object) -> None:
        seen.append(dict(attrs))
        real(**attrs)

    monkeypatch.setattr("omnigent.server.mcp_endpoint.add_audit_attrs", spy)
    with caplog.at_level(logging.INFO):
        called = await tools_call(
            chat_client,
            "chat_post",
            {"channel": billing, "body": _MARKER, "confirm": True},
            token=token,
            protocol="2025-11-25",
        )
    assert called.status_code == 200, called.text
    assert called.json()["result"].get("isError") is not True
    attrs = seen[-1]
    assert attrs["action"] == "mcp.tools_call"
    assert attrs["tool"] == "chat_post"
    assert attrs["principal_kind"] == "agent_account"
    assert attrs["account_id"] == account["id"]
    assert attrs["owner_id"] == JENS
    assert attrs["confirm"] == "true"
    assert attrs["outcome"] == "ok"
    blob = " ".join(str(value) for row in seen for value in row.values())
    assert _MARKER not in blob
    assert _MARKER not in caplog.text


async def test_ac_100b_streak_audit_outcome_is_error(
    chat_client, db_uri: str, monkeypatch
) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    account = created.json()["account"]
    token = created.json()["token"]
    billing = await channel_id(chat_client, JENS, "billing")
    add_member(db_uri, billing, account["principal_id"])
    root = (await post_message(chat_client, JENS, billing, "m1")).json()
    for index in range(5):
        posted = await post_message(
            chat_client,
            account["principal_id"],
            billing,
            f"bot {index}",
            token=token,
            thread_root_id=root["id"],
        )
        assert posted.status_code == 201, posted.text
    seen: list[dict] = []

    def spy(**attrs: object) -> None:
        seen.append(dict(attrs))

    monkeypatch.setattr("omnigent.server.mcp_endpoint.add_audit_attrs", spy)
    called = await tools_call(
        chat_client,
        "chat_post",
        {"channel": billing, "body": "sechste", "thread_root_id": root["id"], "confirm": True},
        token=token,
    )
    assert called.json()["result"]["isError"] is True
    attrs = seen[-1]
    assert attrs["outcome"] == "error"
    assert attrs["error_code"] == "bot_streak_limit"
    assert "sechste" not in " ".join(str(value) for value in attrs.values())


async def test_ac_100c_shell_audit_omits_the_command(
    chat_client, db_uri: str, monkeypatch, caplog
) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    account = created.json()["account"]
    token = created.json()["token"]
    conversation = SqlAlchemyConversationStore(db_uri).create_conversation(
        agent_id=uuid.uuid4().hex
    )
    permissions = SqlAlchemyPermissionStore(db_uri)
    permissions.ensure_user(JENS)
    permissions.grant(JENS, conversation.id, LEVEL_OWNER)
    seen: list[dict] = []
    real = __import__("omnigent.debug_logging", fromlist=["add_audit_attrs"]).add_audit_attrs

    def spy(**attrs: object) -> None:
        seen.append(dict(attrs))
        real(**attrs)

    monkeypatch.setattr("omnigent.server.mcp_endpoint.add_audit_attrs", spy)
    marker = "MARKER-91c2"
    with caplog.at_level(logging.INFO):
        called = await tools_call(
            chat_client,
            "session_workspace",
            {
                "action": "shell",
                "session_id": conversation.id,
                "command": f"echo {marker}",
                "confirm": True,
            },
            token=token,
        )
    assert called.status_code == 200, called.text
    attrs = next(row for row in seen if row.get("action") == "mcp.tools_call")
    assert attrs["tool"] == "session_workspace"
    assert attrs["principal_kind"] == "agent_account"
    assert attrs["account_id"] == account["id"]
    assert attrs["owner_id"] == JENS
    assert attrs["confirm"] == "true"
    assert "outcome" in attrs
    blob = " ".join(str(value) for row in seen for value in row.values())
    assert marker not in blob
    assert marker not in caplog.text


async def test_ac_076b_last_seen_updates_at_most_once_per_minute(chat_client, db_uri: str) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    account = created.json()["account"]
    token = created.json()["token"]
    billing = await channel_id(chat_client, JENS, "billing")
    add_member(db_uri, billing, account["principal_id"])
    stale = now_epoch_us() - 15 * 60 * 1_000_000
    with Session(get_or_create_engine(db_uri)) as session:
        row = session.get(SqlChatAgentAccount, (0, account["id"]))
        assert row is not None
        row.last_seen_at = stale
        session.commit()

    listed = await rpc(chat_client, "tools/list", token=token, protocol="2025-11-25")
    assert listed.status_code == 200, listed.text
    seen = _seen(db_uri, account["id"])
    assert seen is not None
    assert abs(seen - now_epoch_us()) < 2_000_000
    members = await chat_client.get(
        f"/v1/chat/channels/{billing}/members", headers=headers_for(token=token)
    )
    assert members.status_code == 200, members.text
    bot = next(item for item in members.json() if item["principal_id"] == account["principal_id"])
    assert bot["status"] == "aktiv"

    second = await rpc(chat_client, "tools/list", token=token, protocol="2025-11-25")
    assert second.status_code == 200
    assert _seen(db_uri, account["id"]) == seen

    revoked = await chat_client.delete(
        f"/v1/chat/agent-accounts/{account['id']}", headers=headers_for(JENS)
    )
    assert revoked.status_code == 204, revoked.text
    denied = await rpc(chat_client, "tools/list", token=token)
    assert denied.status_code == 401, denied.text
    assert agent_row(db_uri, account["id"]).last_seen_at == seen
