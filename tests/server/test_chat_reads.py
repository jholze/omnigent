"""Read cursors, unread counts, and the read.updated frame (T-101)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from omnigent.db.db_models import SqlChatRead
from omnigent.db.utils import get_or_create_engine
from tests.server.chat_testkit import (
    ANNA,
    JENS,
    channel_id,
    chat_client,
    headers_for,
    join,
    post_message,
)

# Imported for the pytest fixture.
_ = chat_client


def _cursor(db_uri: str, channel: str, principal: str) -> str | None:
    with Session(get_or_create_engine(db_uri)) as session:
        row = session.execute(
            select(SqlChatRead).where(
                SqlChatRead.channel_id == channel,
                SqlChatRead.principal_id == principal,
                SqlChatRead.thread_root_id == "",
            )
        ).scalar_one_or_none()
        return None if row is None else row.last_read_id


async def _read(client, channel: str, message: dict, *, user: str = ANNA):
    return await client.post(
        f"/v1/chat/channels/{channel}/read",
        json={"created_at": message["created_at"], "id": message["id"]},
        headers=headers_for(user),
    )


async def test_ac_007a_read_cursor_moves_forward_to_m12(chat_client, db_uri: str) -> None:
    billing = await channel_id(chat_client, ANNA, "billing")
    await join(chat_client, JENS, billing)
    m10 = (await post_message(chat_client, JENS, billing, "m10")).json()
    m11 = (await post_message(chat_client, JENS, billing, "m11")).json()
    m12 = (await post_message(chat_client, JENS, billing, "m12")).json()
    assert (await _read(chat_client, billing, m10)).status_code == 204

    moved = await _read(chat_client, billing, m12)
    assert moved.status_code == 204, moved.text
    assert _cursor(db_uri, billing, ANNA) == m12["id"]
    assert m11["id"] != m12["id"]


async def test_ac_007b_older_cursor_stays_and_answers_204(chat_client, db_uri: str) -> None:
    billing = await channel_id(chat_client, ANNA, "billing")
    await join(chat_client, JENS, billing)
    m11 = (await post_message(chat_client, JENS, billing, "m11")).json()
    m12 = (await post_message(chat_client, JENS, billing, "m12")).json()
    assert (await _read(chat_client, billing, m12)).status_code == 204

    older = await _read(chat_client, billing, m11)
    assert older.status_code == 204, older.text
    assert older.json()["cursor"]["id"] == m12["id"]
    assert older.json()["advanced"] is False
    assert _cursor(db_uri, billing, ANNA) == m12["id"]


async def test_ac_007c_unknown_message_is_422_and_cursor_unchanged(
    chat_client, db_uri: str
) -> None:
    billing = await channel_id(chat_client, ANNA, "billing")
    kept = (await post_message(chat_client, ANNA, billing, "kept")).json()
    assert (await _read(chat_client, billing, kept)).status_code == 204

    missing = await chat_client.post(
        f"/v1/chat/channels/{billing}/read",
        json={"id": "0" * 32},
        headers=headers_for(ANNA),
    )
    assert missing.status_code == 422, missing.text
    assert missing.json()["error"]["code"] == "invalid_cursor"
    assert _cursor(db_uri, billing, ANNA) == kept["id"]


async def test_ac_008a_bootstrap_counts_other_authors_and_mentions(chat_client) -> None:
    billing = await channel_id(chat_client, ANNA, "billing")
    await join(chat_client, JENS, billing)
    m10 = (await post_message(chat_client, JENS, billing, "m10")).json()
    assert (await _read(chat_client, billing, m10)).status_code == 204
    mentioned = await post_message(chat_client, JENS, billing, f"<@user:{ANNA}> schau mal")
    assert mentioned.status_code == 201, mentioned.text
    own = await post_message(chat_client, ANNA, billing, "m12")
    assert own.status_code == 201, own.text

    payload = await chat_client.get("/v1/chat/bootstrap", headers=headers_for(ANNA))
    assert payload.status_code == 200
    row = next(item for item in payload.json()["channels"] if item["name"] == "billing")
    assert row["unread_count"] == 1
    assert row["mention_count"] == 1


async def test_ac_008b_empty_general_counts_are_zero(chat_client) -> None:
    payload = await chat_client.get("/v1/chat/bootstrap", headers=headers_for(ANNA))
    general = next(item for item in payload.json()["channels"] if item["name"] == "general")
    assert general["unread_count"] == 0
    assert general["mention_count"] == 0


async def test_ac_009b_read_updated_goes_only_to_the_mover(chat_client, monkeypatch) -> None:
    billing = await channel_id(chat_client, ANNA, "billing")
    await join(chat_client, JENS, billing)
    message = (await post_message(chat_client, JENS, billing, "hi")).json()
    recipients: list[str] = []

    def spy(principal_id: str, **kwargs: object) -> None:
        assert "body" not in kwargs
        recipients.append(principal_id)

    monkeypatch.setattr("omnigent.server.routes.chat.publish_read_updated", spy)
    moved = await _read(chat_client, billing, message)
    assert moved.status_code == 204
    assert recipients == [ANNA]
