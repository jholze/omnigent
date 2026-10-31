"""HTTP tests for the team-chat routes under ``/v1/chat``.

Identities come from the ``X-Forwarded-Email`` header. The ``team_chat``
flag is read from ``OMNIGENT_FEATURES`` when the app is built, so each
client fixture sets or clears it before calling ``create_app``.
"""

from __future__ import annotations

import asyncio
import types
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from omnigent.db.db_models import (
    SqlChatAgentAccount,
    SqlChatChannel,
    SqlChatMember,
    SqlChatMessage,
    SqlChatThread,
)
from omnigent.db.utils import get_or_create_engine, now_epoch_us
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.app import create_app
from omnigent.server.auth import UnifiedAuthProvider
from omnigent.server.chat_access import principal_kind
from omnigent.server.feature_flags import FEATURES_ENV_VAR
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.chat_store.sqlalchemy_store import SqlAlchemyChatStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore

ANNA = "anna@bloopark.de"
JENS = "jens@bloopark.de"
BOB = "bob@bloopark.de"
CARLA = "carla@bloopark.de"
GROK_JENS = "agent:grok-jens"


def _headers(user: str) -> dict[str, str]:
    return {"X-Forwarded-Email": user}


def _build_app(db_uri: str, tmp_path: Path) -> FastAPI:
    # No explicit ``feature_flags``: the app resolves them from OMNIGENT_FEATURES.
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    return create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=SqlAlchemyFileStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        artifact_store=artifact_store,
        agent_cache=AgentCache(artifact_store=artifact_store, cache_dir=tmp_path / "cache"),
        permission_store=SqlAlchemyPermissionStore(db_uri),
        auth_provider=UnifiedAuthProvider(source="header"),
    )


@pytest_asyncio.fixture()
async def chat_client(
    db_uri: str,
    runtime_init: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    """Client against an app built with ``team_chat`` on."""
    monkeypatch.setenv(FEATURES_ENV_VAR, "team_chat")
    transport = httpx.ASGITransport(app=_build_app(db_uri, tmp_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture()
async def flag_off_client(
    db_uri: str,
    runtime_init: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    """Client against an app built with ``OMNIGENT_FEATURES`` unset."""
    monkeypatch.delenv(FEATURES_ENV_VAR, raising=False)
    transport = httpx.ASGITransport(app=_build_app(db_uri, tmp_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _count(db_uri: str, model: type, *where: object) -> int:
    with Session(get_or_create_engine(db_uri)) as session:
        return session.scalar(select(func.count()).select_from(model).where(*where)) or 0


def _add_agent_account(db_uri: str, principal_id: str, owner_id: str) -> None:
    with Session(get_or_create_engine(db_uri)) as session:
        session.add(
            SqlChatAgentAccount(
                id=uuid.uuid4().hex,
                principal_id=principal_id,
                owner_id=owner_id,
                display_name="Grok",
                handle="grok-jens",
                token_hash=uuid.uuid4().hex,
                token_prefix="prefix1",
                secret_version=1,
                webhook_url="http://example.invalid",
                created_at=1,
            )
        )
        session.commit()


async def _bootstrap(client: httpx.AsyncClient, user: str) -> dict:
    resp = await client.get("/v1/chat/bootstrap", headers=_headers(user))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _general_id(client: httpx.AsyncClient, user: str) -> str:
    payload = await _bootstrap(client, user)
    (general,) = (c for c in payload["channels"] if c["name"] == "general")
    return general["id"]


async def _create_channel(client: httpx.AsyncClient, user: str, name: str) -> str:
    resp = await client.post("/v1/chat/channels", json={"name": name}, headers=_headers(user))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _join(client: httpx.AsyncClient, user: str, channel_id: str) -> None:
    resp = await client.post(f"/v1/chat/channels/{channel_id}/join", headers=_headers(user))
    assert resp.status_code == 204, resp.text


async def _post(
    client: httpx.AsyncClient, user: str, channel_id: str, body: str, **extra: object
) -> httpx.Response:
    return await client.post(
        f"/v1/chat/channels/{channel_id}/messages",
        json={"body": body, **extra},
        headers=_headers(user),
    )


async def _open_dm(client: httpx.AsyncClient, user: str, others: list[str]) -> httpx.Response:
    return await client.post("/v1/chat/dms", json={"principals": others}, headers=_headers(user))


async def _typing(
    client: httpx.AsyncClient, user: str, channel_id: str, body: dict[str, object]
) -> httpx.Response:
    return await client.post(
        f"/v1/chat/channels/{channel_id}/typing", json=body, headers=_headers(user)
    )


def _record_typing(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, bool]]:
    """Replace the route's ``publish_typing`` and record ``(channel, principal, active)``."""
    published: list[tuple[str, str, bool]] = []

    def record(_store: object, channel: object, principal_id: str, *, active: bool) -> None:
        published.append((channel.id, principal_id, active))  # type: ignore[attr-defined]

    monkeypatch.setattr("omnigent.server.routes.chat.publish_typing", record)
    return published


def _freeze_route_clock(monkeypatch: pytest.MonkeyPatch, start: float = 1000.0) -> list[float]:
    """Pin ``time.monotonic`` inside the chat routes; mutate ``[0]`` to advance it."""
    clock = [start]
    monkeypatch.setattr(
        "omnigent.server.routes.chat.time", types.SimpleNamespace(monotonic=lambda: clock[0])
    )
    return clock


async def test_ac_003a_post_is_404_and_stores_nothing_when_flag_off(
    flag_off_client: httpx.AsyncClient, db_uri: str
) -> None:
    general = SqlAlchemyChatStore(db_uri).bootstrap_channel("general", ANNA)

    resp = await _post(flag_off_client, ANNA, general.id, "Hallo")

    assert resp.status_code == 404, resp.text
    assert _count(db_uri, SqlChatMessage) == 0


async def test_ac_003b_mcp_tools_list_has_no_chat_tools_when_flag_off(
    flag_off_client: httpx.AsyncClient,
) -> None:
    resp = await flag_off_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=_headers(ANNA),
    )

    assert resp.status_code == 200, resp.text
    names = [tool["name"] for tool in resp.json()["result"]["tools"]]
    assert names, "tools/list returned no tools; the check below would be vacuous"
    assert not [name for name in names if name.startswith("chat_")]


async def test_ac_003c_malformed_channel_body_is_404_not_422_when_flag_off(
    flag_off_client: httpx.AsyncClient, db_uri: str
) -> None:
    resp = await flag_off_client.post("/v1/chat/channels", json={}, headers=_headers(ANNA))

    assert resp.status_code == 404, resp.text
    assert _count(db_uri, SqlChatChannel) == 0


async def test_ac_003d_malformed_messages_query_is_404_not_422_when_flag_off(
    flag_off_client: httpx.AsyncClient,
) -> None:
    resp = await flag_off_client.get(
        "/v1/chat/channels/nope/messages", params={"limit": "abc"}, headers=_headers(ANNA)
    )

    assert resp.status_code == 404, resp.text


async def test_ac_004a_concurrent_bootstrap_creates_one_general_with_both_members(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    anna, jens = await asyncio.gather(_bootstrap(chat_client, ANNA), _bootstrap(chat_client, JENS))

    assert _count(db_uri, SqlChatChannel, SqlChatChannel.name == "general") == 1
    general_ids = {
        c["id"] for payload in (anna, jens) for c in payload["channels"] if c["name"] == "general"
    }
    assert len(general_ids) == 1
    (general_id,) = general_ids
    store = SqlAlchemyChatStore(db_uri)
    assert store.is_member(general_id, ANNA)
    assert store.is_member(general_id, JENS)


async def test_ac_004b_agent_principal_bootstrap_does_not_join_general(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    # Agent bearer tokens are not wired yet; the header identity carries the
    # ``agent:`` principal id that the route classifies.
    assert principal_kind(GROK_JENS) == "agent_account"
    general_id = await _general_id(chat_client, ANNA)

    payload = await _bootstrap(chat_client, GROK_JENS)

    assert payload["me"] == GROK_JENS
    assert not [c for c in payload["channels"] if c["name"] == "general"]
    assert not SqlAlchemyChatStore(db_uri).is_member(general_id, GROK_JENS)


async def test_ac_005a_create_channel_returns_201_and_creator_is_member(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    resp = await chat_client.post(
        "/v1/chat/channels", json={"name": "billing-ops"}, headers=_headers(ANNA)
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "billing-ops"
    assert SqlAlchemyChatStore(db_uri).is_member(resp.json()["id"], ANNA)


@pytest.mark.parametrize(
    ("name", "status"),
    [("Billing Ops!", 422), ("billing", 409)],
    ids=["invalid-name", "duplicate-name"],
)
async def test_ac_005b_invalid_or_duplicate_channel_name_creates_nothing(
    chat_client: httpx.AsyncClient, db_uri: str, name: str, status: int
) -> None:
    await _create_channel(chat_client, JENS, "billing")
    before = _count(db_uri, SqlChatChannel)

    resp = await chat_client.post("/v1/chat/channels", json={"name": name}, headers=_headers(ANNA))

    assert resp.status_code == status, resp.text
    assert _count(db_uri, SqlChatChannel) == before


async def test_ac_006a_join_makes_carla_member_and_lists_channel(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    billing_id = await _create_channel(chat_client, ANNA, "billing")
    assert billing_id not in {c["id"] for c in (await _bootstrap(chat_client, CARLA))["channels"]}

    await _join(chat_client, CARLA, billing_id)

    assert SqlAlchemyChatStore(db_uri).is_member(billing_id, CARLA)
    sidebar = await _bootstrap(chat_client, CARLA)
    assert billing_id in {c["id"] for c in sidebar["channels"]}


async def test_ac_006b_leaving_general_is_409_and_keeps_membership(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    general_id = await _general_id(chat_client, ANNA)

    resp = await chat_client.post(f"/v1/chat/channels/{general_id}/leave", headers=_headers(ANNA))

    assert resp.status_code == 409, resp.text
    assert SqlAlchemyChatStore(db_uri).is_member(general_id, ANNA)


async def test_ac_013a_posted_message_is_readable_by_another_member(
    chat_client: httpx.AsyncClient,
) -> None:
    general_id = await _general_id(chat_client, ANNA)
    await _bootstrap(chat_client, JENS)

    posted = await _post(chat_client, ANNA, general_id, "Deploy heute 16 Uhr?")
    assert posted.status_code == 201, posted.text

    listed = await chat_client.get(
        f"/v1/chat/channels/{general_id}/messages",
        params={"tail": "1"},
        headers=_headers(JENS),
    )
    assert listed.status_code == 200, listed.text
    (item,) = listed.json()["items"]
    assert item["body"] == "Deploy heute 16 Uhr?"
    assert item["author_id"] == ANNA
    assert isinstance(item["created_at"], int)


async def test_ac_013b_body_is_trimmed_and_author_is_server_set(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    general_id = await _general_id(chat_client, ANNA)

    resp = await _post(
        chat_client, ANNA, general_id, "  Hallo  ", client_id="c-1", created_by=JENS
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["body"] == "Hallo"
    assert resp.json()["author_id"] == ANNA
    with Session(get_or_create_engine(db_uri)) as session:
        row = session.scalars(select(SqlChatMessage)).one()
        assert (row.body, row.author_id) == ("Hallo", ANNA)


async def test_ac_015a_whitespace_body_is_422_and_stores_nothing(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    general_id = await _general_id(chat_client, ANNA)

    resp = await _post(chat_client, ANNA, general_id, "   ")

    assert resp.status_code == 422, resp.text
    assert _count(db_uri, SqlChatMessage) == 0


@pytest.mark.parametrize("body", ["a" * 4001, "a\u0000b"], ids=["4001-chars", "nul"])
async def test_ac_015b_overlong_or_nul_body_is_422_and_stores_nothing(
    chat_client: httpx.AsyncClient, db_uri: str, body: str
) -> None:
    general_id = await _general_id(chat_client, ANNA)

    resp = await _post(chat_client, ANNA, general_id, body)

    assert resp.status_code == 422, resp.text
    assert _count(db_uri, SqlChatMessage) == 0


async def test_ac_015c_body_of_exactly_4000_chars_is_201(
    chat_client: httpx.AsyncClient,
) -> None:
    general_id = await _general_id(chat_client, ANNA)

    resp = await _post(chat_client, ANNA, general_id, "a" * 4000)

    assert resp.status_code == 201, resp.text


async def test_ac_016a_non_member_post_is_403_not_a_member(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    billing_id = await _create_channel(chat_client, ANNA, "billing")

    resp = await _post(chat_client, CARLA, billing_id, "Hi")

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "not_a_member"
    assert _count(db_uri, SqlChatMessage) == 0


async def test_ac_016b_non_participant_post_in_dm_is_404(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    dm = await _open_dm(chat_client, ANNA, [JENS])
    assert dm.status_code == 201, dm.text

    resp = await _post(chat_client, BOB, dm.json()["id"], "Hi")

    assert resp.status_code == 404, resp.text
    assert _count(db_uri, SqlChatMessage) == 0


async def test_ac_019a_tail_returns_newest_50_ascending_with_has_older(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    general_id = await _general_id(chat_client, ANNA)
    store = SqlAlchemyChatStore(db_uri)
    posted = [store.post_message(general_id, ANNA, f"m{i}").message for i in range(120)]
    newest = sorted(posted, key=lambda m: (m.created_at, m.id))[-50:]

    resp = await chat_client.get(
        f"/v1/chat/channels/{general_id}/messages",
        params={"tail": "1"},
        headers=_headers(ANNA),
    )

    assert resp.status_code == 200, resp.text
    page = resp.json()
    assert [item["id"] for item in page["items"]] == [m.id for m in newest]
    keys = [(item["created_at"], item["id"]) for item in page["items"]]
    assert keys == sorted(keys)
    assert page["has_older"] is True


@pytest.mark.parametrize(
    "params",
    [
        {"before": "1:a", "after": "1:a"},
        {"tail": "1", "limit": "500"},
    ],
    ids=["before-and-after", "limit-500"],
)
async def test_ac_019b_conflicting_cursors_or_large_limit_is_422(
    chat_client: httpx.AsyncClient, params: dict[str, str]
) -> None:
    general_id = await _general_id(chat_client, ANNA)

    resp = await chat_client.get(
        f"/v1/chat/channels/{general_id}/messages", params=params, headers=_headers(ANNA)
    )

    assert resp.status_code == 422, resp.text


async def test_ac_021a_reopening_dm_returns_same_id_and_jens_sees_it(
    chat_client: httpx.AsyncClient,
) -> None:
    first = await _open_dm(chat_client, ANNA, [JENS])
    second = await _open_dm(chat_client, ANNA, [JENS])
    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    dm_id = first.json()["id"]
    assert second.json()["id"] == dm_id

    posted = await _post(chat_client, ANNA, dm_id, "Hi Jens")
    assert posted.status_code == 201, posted.text

    jens = await _bootstrap(chat_client, JENS)
    assert dm_id in {dm["id"] for dm in jens["dms"]}


async def test_ac_021b_dm_with_nine_others_is_422(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    others = [f"user{i}@bloopark.de" for i in range(9)]

    resp = await _open_dm(chat_client, ANNA, others)

    assert resp.status_code == 422, resp.text
    assert _count(db_uri, SqlChatChannel, SqlChatChannel.kind == "dm") == 0


async def test_ac_021c_dm_with_agent_without_owner_is_403_and_creates_nothing(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    _add_agent_account(db_uri, GROK_JENS, JENS)

    resp = await _open_dm(chat_client, ANNA, [GROK_JENS])

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "agent_owner_required"
    assert _count(db_uri, SqlChatChannel, SqlChatChannel.kind == "dm") == 0
    assert _count(db_uri, SqlChatMember, SqlChatMember.principal_id == GROK_JENS) == 0


async def test_ac_022a_non_participant_reading_dm_is_404(
    chat_client: httpx.AsyncClient,
) -> None:
    dm = await _open_dm(chat_client, ANNA, [JENS])
    assert dm.status_code == 201, dm.text

    resp = await chat_client.get(
        f"/v1/chat/channels/{dm.json()['id']}/messages",
        params={"tail": "1"},
        headers=_headers(BOB),
    )

    assert resp.status_code == 404, resp.text


async def test_ac_023b_thread_root_message_carries_agent_state_reply_does_not(
    chat_client: httpx.AsyncClient, db_uri: str
) -> None:
    """
    The channel view surfaces ``agent_state`` on a thread root only.

    A top-level message with a ``chat_threads`` row reads its stored
    ``agent_state``; a reply to that root stays without the field, and
    a root with no thread row also omits it (no state to populate).
    """
    general_id = await _general_id(chat_client, ANNA)
    root = await _post(chat_client, ANNA, general_id, "Root with an agent thread")
    assert root.status_code == 201, root.text
    root_id = root.json()["id"]

    # Attach a thread row (as the agent-start path would) with a live state.
    with Session(get_or_create_engine(db_uri)) as session:
        session.add(
            SqlChatThread(
                root_message_id=root_id,
                channel_id=general_id,
                reply_count=0,
                participants="[]",
                bot_streak=0,
                agent_state="active",
                updated_at=now_epoch_us(),
            )
        )
        session.commit()

    reply = await _post(chat_client, ANNA, general_id, "A reply", thread_root_id=root_id)
    assert reply.status_code == 201, reply.text

    # A second root with no thread row must not fabricate an agent_state.
    rootless = await _post(chat_client, ANNA, general_id, "Root with no thread row")
    assert rootless.status_code == 201, rootless.text
    rootless_id = rootless.json()["id"]

    listed = await chat_client.get(
        f"/v1/chat/channels/{general_id}/messages",
        headers=_headers(ANNA),
    )
    assert listed.status_code == 200, listed.text
    by_id = {item["id"]: item for item in listed.json()["items"]}

    assert by_id[root_id]["agent_state"] == "active"
    assert "agent_state" not in by_id[rootless_id]

    # The reply view (thread_root_id set) must not carry agent_state.
    thread = await chat_client.get(
        f"/v1/chat/channels/{general_id}/messages",
        params={"thread_root_id": root_id},
        headers=_headers(ANNA),
    )
    assert thread.status_code == 200, thread.text
    (reply_item,) = thread.json()["items"]
    assert reply_item["id"] == reply.json()["id"]
    assert "agent_state" not in reply_item


async def test_ac_032a_typing_throttle(
    chat_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second ``active: true`` inside 3 s is accepted but not published."""
    general_id = await _general_id(chat_client, ANNA)
    await _bootstrap(chat_client, JENS)
    published = _record_typing(monkeypatch)
    clock = _freeze_route_clock(monkeypatch)

    first = await _typing(chat_client, ANNA, general_id, {"active": True})
    clock[0] += 2.9
    second = await _typing(chat_client, ANNA, general_id, {"active": True})

    assert (first.status_code, second.status_code) == (204, 204)
    assert published == [(general_id, ANNA, True)]


async def test_ac_032a_typing_throttle_window_reopens_after_3s(
    chat_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    general_id = await _general_id(chat_client, ANNA)
    published = _record_typing(monkeypatch)
    clock = _freeze_route_clock(monkeypatch)

    await _typing(chat_client, ANNA, general_id, {"active": True})
    clock[0] += 3.0
    resp = await _typing(chat_client, ANNA, general_id, {"active": True})

    assert resp.status_code == 204, resp.text
    assert published == [(general_id, ANNA, True), (general_id, ANNA, True)]


async def test_ac_032a_typing_throttle_is_per_principal(
    chat_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    general_id = await _general_id(chat_client, ANNA)
    await _bootstrap(chat_client, JENS)
    published = _record_typing(monkeypatch)
    _freeze_route_clock(monkeypatch)

    await _typing(chat_client, ANNA, general_id, {"active": True})
    await _typing(chat_client, JENS, general_id, {"active": True})

    assert published == [(general_id, ANNA, True), (general_id, JENS, True)]


async def test_ac_032a_typing_inactive_is_always_published(
    chat_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    general_id = await _general_id(chat_client, ANNA)
    published = _record_typing(monkeypatch)
    _freeze_route_clock(monkeypatch)

    await _typing(chat_client, ANNA, general_id, {"active": True})
    first_stop = await _typing(chat_client, ANNA, general_id, {"active": False})
    second_stop = await _typing(chat_client, ANNA, general_id, {"active": False})

    assert (first_stop.status_code, second_stop.status_code) == (204, 204)
    assert published == [
        (general_id, ANNA, True),
        (general_id, ANNA, False),
        (general_id, ANNA, False),
    ]


async def test_ac_032a_typing_non_member_is_403_not_a_member(
    chat_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    billing_id = await _create_channel(chat_client, ANNA, "billing")
    published = _record_typing(monkeypatch)

    resp = await _typing(chat_client, CARLA, billing_id, {"active": True})

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "not_a_member"
    assert published == []


async def test_ac_032a_typing_non_participant_in_dm_is_404(
    chat_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    dm = await _open_dm(chat_client, ANNA, [JENS])
    assert dm.status_code == 201, dm.text
    published = _record_typing(monkeypatch)

    resp = await _typing(chat_client, BOB, dm.json()["id"], {"active": True})

    assert resp.status_code == 404, resp.text
    assert published == []


async def test_ac_032a_typing_unknown_channel_is_404(chat_client: httpx.AsyncClient) -> None:
    resp = await _typing(chat_client, ANNA, "nope", {"active": True})

    assert resp.status_code == 404, resp.text


async def test_ac_032a_typing_is_404_and_stores_nothing_when_flag_off(
    flag_off_client: httpx.AsyncClient, db_uri: str
) -> None:
    general = SqlAlchemyChatStore(db_uri).bootstrap_channel("general", ANNA)
    members_before = _count(db_uri, SqlChatMember)

    resp = await _typing(flag_off_client, ANNA, general.id, {"active": True})

    assert resp.status_code == 404, resp.text
    assert _count(db_uri, SqlChatMessage) == 0
    assert _count(db_uri, SqlChatChannel) == 1
    assert _count(db_uri, SqlChatMember) == members_before


@pytest.mark.parametrize(
    "body",
    [{}, {"active": "true"}, {"active": 1}, {"active": None}],
    ids=["missing", "string", "int", "null"],
)
async def test_ac_032a_typing_non_boolean_active_is_422(
    chat_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, body: dict[str, object]
) -> None:
    general_id = await _general_id(chat_client, ANNA)
    published = _record_typing(monkeypatch)

    resp = await _typing(chat_client, ANNA, general_id, body)

    assert resp.status_code == 422, resp.text
    assert published == []


async def test_ac_032a_typing_draft_field_is_ignored_and_stores_nothing(
    chat_client: httpx.AsyncClient, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TypingBody uses the default pydantic config: extra keys are dropped."""
    general_id = await _general_id(chat_client, ANNA)
    published = _record_typing(monkeypatch)

    resp = await _typing(
        chat_client, ANNA, general_id, {"active": True, "draft": "geheimer Entwurf"}
    )

    assert resp.status_code == 204, resp.text
    assert resp.content == b""
    assert published == [(general_id, ANNA, True)]
    assert _count(db_uri, SqlChatMessage) == 0
