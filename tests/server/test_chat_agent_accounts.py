"""Creating, limiting, and revoking agent accounts (T-105)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from omnigent.db.db_models import SqlChatAgentAccount
from omnigent.db.utils import get_or_create_engine
from omnigent.server.feature_flags import FEATURES_ENV_VAR
from tests.server.chat_testkit import (
    ANNA,
    JENS,
    WEBHOOK_OK,
    build_chat_app,
    chat_client,
    create_account,
    headers_for,
)

_ = chat_client


async def test_ac_061b_duplicate_handle_is_409(chat_client) -> None:
    first = await create_account(chat_client, JENS, "grok-jens")
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["token"].startswith("ogc_")
    assert len(body["token"]) > len("ogc_") + 40
    assert body["webhook_secret"]
    assert "token" not in body["account"]
    assert body["account"]["handle"] == "grok-jens"

    taken = await create_account(chat_client, ANNA, "grok-jens", display_name="Other")
    assert taken.status_code == 409, taken.text


async def test_ac_063a_sixth_account_is_agent_account_limit(chat_client) -> None:
    for index in range(5):
        created = await create_account(chat_client, JENS, f"grok-{index}")
        assert created.status_code == 201, created.text
    sixth = await create_account(chat_client, JENS, "grok-6")
    assert sixth.status_code == 409, sixth.text
    assert sixth.json()["error"]["code"] == "agent_account_limit"


async def test_ac_064b_non_owner_delete_is_404_and_account_stays_active(
    chat_client,
) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    assert created.status_code == 201, created.text
    account_id = created.json()["account"]["id"]
    token = created.json()["token"]

    denied = await chat_client.delete(
        f"/v1/chat/agent-accounts/{account_id}", headers=headers_for(ANNA)
    )
    assert denied.status_code == 404, denied.text
    still = await chat_client.get("/v1/chat/channels", headers=headers_for(token=token))
    assert still.status_code == 200, still.text


async def test_ac_065a_webhook_must_be_https_and_public(chat_client) -> None:
    http_hook = await create_account(
        chat_client, JENS, "grok-http", webhook_url="http://bot.example.com/hook"
    )
    internal = await create_account(
        chat_client, JENS, "grok-internal", webhook_url="https://10.0.0.5/hook"
    )
    assert http_hook.status_code == 422, http_hook.text
    assert internal.status_code == 422, internal.text
    assert http_hook.json()["error"]["code"] == "invalid_webhook"


async def test_missing_secret_is_503_agent_accounts_unconfigured(
    db_uri, runtime_init, tmp_path, monkeypatch
) -> None:
    del runtime_init
    monkeypatch.setenv(FEATURES_ENV_VAR, "team_chat")
    monkeypatch.delenv("OMNIGENT_CHAT_AGENT_SECRET", raising=False)
    import httpx

    transport = httpx.ASGITransport(app=build_chat_app(db_uri, tmp_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await create_account(client, JENS, "grok-jens", webhook_url=WEBHOOK_OK)
    assert resp.status_code == 503, resp.text
    assert resp.json()["error"]["code"] == "agent_accounts_unconfigured"


async def test_create_audit_omits_token_and_webhook(chat_client, monkeypatch, db_uri) -> None:
    seen: list[dict] = []

    def spy(**attrs: object) -> None:
        seen.append(dict(attrs))

    monkeypatch.setattr("omnigent.server.routes.chat.add_audit_attrs", spy)
    created = await create_account(chat_client, JENS, "grok-jens")
    assert created.status_code == 201, created.text
    token = created.json()["token"]
    blob = " ".join(str(value) for row in seen for value in row.values())
    assert token not in blob
    assert WEBHOOK_OK not in blob
    assert seen[-1]["action"] == "chat.agent_account.create"
    assert seen[-1]["outcome"] == "created"
    with Session(get_or_create_engine(db_uri)) as session:
        assert session.query(SqlChatAgentAccount).count() == 1
