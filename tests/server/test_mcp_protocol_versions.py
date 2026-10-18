"""Protocol negotiation, header checks, and unknown methods (T-126)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from omnigent.db.db_models import SqlChatMessage
from omnigent.db.utils import get_or_create_engine
from tests.server.chat_testkit import (
    JENS,
    add_member,
    channel_id,
    chat_client,
    create_account,
    rpc,
    tools_call,
)

_ = chat_client


async def test_ac_097a_supported_initialize_versions_are_echoed(chat_client) -> None:
    for version in ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"):
        response = await rpc(
            chat_client,
            "initialize",
            {"protocolVersion": version},
            user=JENS,
            protocol=version,
        )
        assert response.status_code == 200, response.text
        assert response.json()["result"]["protocolVersion"] == version
        assert "error" not in response.json()


async def test_ac_097b_future_initialize_version_answers_2025_11_25(chat_client) -> None:
    response = await rpc(
        chat_client,
        "initialize",
        {"protocolVersion": "2026-07-28"},
        user=JENS,
        protocol="2025-11-25",
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["protocolVersion"] == "2025-11-25"
    assert "error" not in response.json()


async def test_ac_098a_invalid_protocol_header_is_http_400(chat_client, db_uri: str) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    token = created.json()["token"]
    billing = await channel_id(chat_client, JENS, "billing")
    add_member(db_uri, billing, created.json()["account"]["principal_id"])
    with Session(get_or_create_engine(db_uri)) as session:
        before = int(session.scalar(select(func.count()).select_from(SqlChatMessage)) or 0)
    response = await tools_call(
        chat_client,
        "chat_post",
        {"channel": billing, "body": "nope", "confirm": True},
        token=token,
        protocol="2099-01-01",
    )
    assert response.status_code == 400, response.text
    assert "error" in response.json()
    with Session(get_or_create_engine(db_uri)) as session:
        after = int(session.scalar(select(func.count()).select_from(SqlChatMessage)) or 0)
    assert after == before


async def test_ac_098b_missing_protocol_header_omits_output_schema(chat_client) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    response = await rpc(chat_client, "tools/list", token=created.json()["token"], protocol=None)
    assert response.status_code == 200, response.text
    tools = response.json()["result"]["tools"]
    assert tools
    assert all("outputSchema" not in tool for tool in tools)


async def test_ac_099a_unknown_methods_are_32601(chat_client) -> None:
    for method in ("foo/bar", "server/discover"):
        response = await rpc(chat_client, method, user=JENS, rpc_id=7)
        assert response.status_code == 200, response.text
        error = response.json()["error"]
        assert error["code"] == -32601
        assert response.json()["id"] == 7


async def test_json_rpc_batch_is_rejected(chat_client) -> None:
    response = await chat_client.post(
        "/mcp",
        json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}],
        headers={"X-Forwarded-Email": JENS},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == -32600
