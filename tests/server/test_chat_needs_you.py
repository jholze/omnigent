"""``GET /v1/chat/needs-you``: approvals only for callers who may answer (T-112)."""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy.orm import Session

from omnigent.db.db_models import SqlChatThread
from omnigent.db.utils import get_or_create_engine, now_epoch_us
from omnigent.runtime import pending_elicitations
from omnigent.server.auth import LEVEL_EDIT, LEVEL_READ
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from tests.server.chat_testkit import (
    ANNA,
    BOB,
    channel_id,
    chat_client,
    headers_for,
    join,
    post_message,
)

_ = chat_client


async def _anna_waiting_agent_thread(client: httpx.AsyncClient, db_uri: str) -> str:
    """#billing root by Anna, linked to her session with one pending elicitation.

    Anna holds EDIT on the session, Bob only READ. Returns the root message id.
    """
    billing = await channel_id(client, ANNA, "billing")
    await join(client, BOB, billing)
    root = await post_message(client, ANNA, billing, "Agent, bitte Rechnung prüfen")
    assert root.status_code == 201, root.text
    root_id = root.json()["id"]
    session_id = uuid.uuid4().hex
    with Session(get_or_create_engine(db_uri)) as session:
        session.add(
            SqlChatThread(
                root_message_id=root_id,
                channel_id=billing,
                reply_count=0,
                participants="[]",
                bot_streak=0,
                agent_state="active",
                session_id=session_id,
                updated_at=now_epoch_us(),
            )
        )
        session.commit()
    permissions = SqlAlchemyPermissionStore(db_uri)
    for user, level in ((ANNA, LEVEL_EDIT), (BOB, LEVEL_READ)):
        permissions.ensure_user(user)
        permissions.grant(user, session_id, level)
    pending_elicitations.record_publish(
        session_id, {"type": "response.elicitation_request", "elicitation_id": "t112-approval"}
    )
    return root_id


async def _needs_you(client: httpx.AsyncClient, user: str) -> dict:
    resp = await client.get("/v1/chat/needs-you", headers=headers_for(user))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_ac_010b_read_only_member_does_not_get_the_approval(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    await _anna_waiting_agent_thread(chat_client, db_uri)

    assert (await _needs_you(chat_client, BOB))["approvals"] == []


async def test_ac_010b_member_who_may_answer_gets_the_approval(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    root_id = await _anna_waiting_agent_thread(chat_client, db_uri)

    approvals = (await _needs_you(chat_client, ANNA))["approvals"]

    assert [row["root_message_id"] for row in approvals] == [root_id]
