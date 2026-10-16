"""Human chat beside a shared agent session.

Covers the server half of issue #3: store on the root, access, the cursor,
and a ``session.human_chat`` event that carries no message text.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import TypeAdapter

from omnigent.runtime import session_stream
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.app import create_app
from omnigent.server.auth import LEVEL_READ, RESERVED_USER_PUBLIC
from omnigent.server.principals import set_principal_resolver
from omnigent.server.schemas import ServerStreamEvent
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.comment_store.sqlalchemy_store import SqlAlchemyCommentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from omnigent.stores.session_chat_store.sqlalchemy_store import SqlAlchemySessionChatStore
from tests.server.helpers import build_agent_bundle

_ADAPTER = TypeAdapter(ServerStreamEvent)


def _headers(user: str) -> dict[str, str]:
    return {"X-Forwarded-Email": user}


async def _create_session(client: httpx.AsyncClient, user: str) -> str:
    """Create a session as ``user`` and return its id."""
    bundle = build_agent_bundle(name="test-agent")
    resp = await client.post(
        "/v1/sessions",
        data={"metadata": "{}"},
        files={"bundle": ("agent.tar.gz", bundle, "application/gzip")},
        headers=_headers(user),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


async def _post(
    client: httpx.AsyncClient, session_id: str, user: str, body: str
) -> httpx.Response:
    return await client.post(
        f"/v1/sessions/{session_id}/chat",
        json={"body": body},
        headers=_headers(user),
    )


async def _get(
    client: httpx.AsyncClient,
    session_id: str,
    user: str,
    *,
    after: str | None = None,
) -> httpx.Response:
    params = {"after": after} if after is not None else None
    return await client.get(
        f"/v1/sessions/{session_id}/chat",
        params=params,
        headers=_headers(user),
    )


def _grant(db_uri: str, user_id: str, session_id: str, level: int = LEVEL_READ) -> None:
    store = SqlAlchemyPermissionStore(db_uri)
    store.ensure_user(user_id)
    store.grant(user_id, session_id, level)


async def test_owner_posts_and_reads_the_line(auth_client: httpx.AsyncClient) -> None:
    """The owner stores a line and reads ``{ items, shared }`` back."""
    session_id = await _create_session(auth_client, "alice@example.com")
    posted = await _post(auth_client, session_id, "alice@example.com", "I'll look at the test")
    assert posted.status_code == 201, posted.text
    body = posted.json()
    assert body["body"] == "I'll look at the test"
    assert body["created_by"] == "alice@example.com"
    assert "id" in body
    assert isinstance(body["created_at"], int)

    listed = await _get(auth_client, session_id, "alice@example.com")
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    assert set(payload) == {"items", "shared"}
    assert payload["shared"] is False
    assert len(payload["items"]) == 1
    assert payload["items"][0]["id"] == body["id"]
    assert payload["items"][0]["body"] == "I'll look at the test"


async def test_child_session_reads_and_writes_the_root(
    auth_client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """A child id stores and returns the root's lines."""
    root_id = await _create_session(auth_client, "alice@example.com")
    child = SqlAlchemyConversationStore(db_uri).create_conversation(
        kind="sub_agent",
        title="researcher",
        parent_conversation_id=root_id,
    )
    assert child.root_conversation_id == root_id

    posted = await _post(auth_client, child.id, "alice@example.com", "from the child")
    assert posted.status_code == 201, posted.text

    on_root = await _get(auth_client, root_id, "alice@example.com")
    on_child = await _get(auth_client, child.id, "alice@example.com")
    assert on_root.status_code == 200, on_root.text
    assert on_child.status_code == 200, on_child.text
    assert [row["id"] for row in on_root.json()["items"]] == [posted.json()["id"]]
    assert [row["body"] for row in on_child.json()["items"]] == ["from the child"]


async def test_same_microsecond_cursor_keeps_both_rows(
    auth_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two rows with one timestamp both come back, and ``after`` is exclusive."""
    monkeypatch.setattr(
        "omnigent.stores.session_chat_store.sqlalchemy_store.now_epoch_us",
        lambda: 1_700_000_000_000_000,
    )
    session_id = await _create_session(auth_client, "alice@example.com")
    first = await _post(auth_client, session_id, "alice@example.com", "one")
    second = await _post(auth_client, session_id, "alice@example.com", "two")
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["created_at"] == second.json()["created_at"]

    listed = await _get(auth_client, session_id, "alice@example.com")
    items = listed.json()["items"]
    assert [row["body"] for row in items] == sorted(
        ["one", "two"],
        key=lambda body: next(row["id"] for row in items if row["body"] == body),
    )
    assert [row["created_at"] for row in items] == [1_700_000_000_000_000, 1_700_000_000_000_000]
    assert items[0]["id"] < items[1]["id"]

    cursor = f"{items[0]['created_at']}:{items[0]['id']}"
    rest = await _get(auth_client, session_id, "alice@example.com", after=cursor)
    assert rest.status_code == 200, rest.text
    assert [row["id"] for row in rest.json()["items"]] == [items[1]["id"]]


async def test_access_direct_grant_public_stranger_and_admin(
    auth_client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """Direct and linked grants may post. Public is 403. A stranger is 404. An admin may post."""
    session_id = await _create_session(auth_client, "alice@example.com")
    stranger = await _post(auth_client, session_id, "dave@example.com", "no")
    assert stranger.status_code == 404, stranger.text

    _grant(db_uri, RESERVED_USER_PUBLIC, session_id)
    public = await _post(auth_client, session_id, "carol@example.com", "no")
    assert public.status_code == 403, public.text
    public_get = await _get(auth_client, session_id, "carol@example.com")
    assert public_get.status_code == 403, public_get.text
    untouched = await _get(auth_client, session_id, "alice@example.com")
    assert untouched.json()["items"] == []
    assert untouched.json()["shared"] is False

    _grant(db_uri, "bob@example.com", session_id)
    direct = await _post(auth_client, session_id, "bob@example.com", "from bob")
    assert direct.status_code == 201, direct.text

    linked_session = await _create_session(auth_client, "alice@example.com")
    _grant(db_uri, "linked-erin@example.com", linked_session)

    def _linked(sub: str) -> frozenset[str]:
        if sub == "erin@example.com":
            return frozenset({"linked-erin@example.com"})
        return frozenset()

    set_principal_resolver(_linked)
    try:
        linked = await _post(
            auth_client, linked_session, "erin@example.com", "from the linked email"
        )
        assert linked.status_code == 201, linked.text
    finally:
        set_principal_resolver(None)

    SqlAlchemyPermissionStore(db_uri).ensure_user("admin@example.com", is_admin=True)
    admin_session = await _create_session(auth_client, "alice@example.com")
    admin = await _post(auth_client, admin_session, "admin@example.com", "from admin")
    assert admin.status_code == 201, admin.text


@pytest.mark.parametrize(
    "body",
    ["", "   \n\t", "x" * 2001],
)
async def test_empty_and_overlong_bodies_are_rejected(
    auth_client: httpx.AsyncClient,
    body: str,
) -> None:
    """Empty, whitespace-only, and 2001-character bodies write nothing."""
    session_id = await _create_session(auth_client, "alice@example.com")
    rejected = await _post(auth_client, session_id, "alice@example.com", body)
    assert rejected.status_code == 422, rejected.text
    listed = await _get(auth_client, session_id, "alice@example.com")
    assert listed.json()["items"] == []


async def test_post_does_not_write_a_conversation_item(
    auth_client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """The line stays out of the agent transcript."""
    session_id = await _create_session(auth_client, "alice@example.com")
    store = SqlAlchemyConversationStore(db_uri)
    before = [item.id for item in store.list_items(session_id, limit=100).data]

    posted = await _post(auth_client, session_id, "alice@example.com", "not for the agent")
    assert posted.status_code == 201, posted.text

    after = store.list_items(session_id, limit=100).data
    assert [item.id for item in after] == before
    rendered = json.dumps(
        [
            item.model_dump() if hasattr(item, "model_dump") else item.__dict__
            for item in after
        ]
    )
    assert "not for the agent" not in rendered


def _human_chat_frames(body: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for line in body.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        payload = json.loads(line[len("data: ") :])
        if payload.get("type") == "session.human_chat":
            frames.append(payload)
    return frames


async def _wait_for_subscribers(
    conversation_id: str, count: int, tasks: list[asyncio.Task[Any]]
) -> None:
    for _ in range(400):
        if session_stream.subscriber_count(conversation_id) >= count:
            return
        for task in tasks:
            if task.done():
                response = task.result()
                raise AssertionError(
                    f"stream for {conversation_id} ended before subscribe: "
                    f"{response.status_code} {response.text[:400]}"
                )
        await asyncio.sleep(0.02)
    raise AssertionError(
        f"timed out waiting for {count} subscribers on {conversation_id}; "
        f"have {session_stream.subscriber_count(conversation_id)}"
    )


async def _end_stream(session_id: str, task: asyncio.Task[Any]) -> httpx.Response:
    for _ in range(200):
        session_stream.close(session_id)
        if task.done():
            break
        await asyncio.sleep(0.01)
    return await asyncio.wait_for(task, 5.0)


async def test_streams_receive_a_bodyless_event_and_public_does_not(
    auth_client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """Root and child streams get one event each. A public viewer gets none."""
    root_id = await _create_session(auth_client, "alice@example.com")
    child = SqlAlchemyConversationStore(db_uri).create_conversation(
        kind="sub_agent",
        title="researcher",
        parent_conversation_id=root_id,
    )
    _grant(db_uri, "bob@example.com", root_id)
    _grant(db_uri, RESERVED_USER_PUBLIC, root_id)

    alice = asyncio.create_task(
        auth_client.get(
            f"/v1/sessions/{root_id}/stream",
            headers=_headers("alice@example.com"),
            timeout=30.0,
        )
    )
    carol = asyncio.create_task(
        auth_client.get(
            f"/v1/sessions/{root_id}/stream",
            headers=_headers("carol@example.com"),
            timeout=30.0,
        )
    )
    bob = asyncio.create_task(
        auth_client.get(
            f"/v1/sessions/{child.id}/stream",
            headers=_headers("bob@example.com"),
            timeout=30.0,
        )
    )
    try:
        await _wait_for_subscribers(root_id, 2, [alice, carol])
        await _wait_for_subscribers(child.id, 1, [bob])
        posted = await _post(auth_client, root_id, "alice@example.com", "I'll look at the test")
        assert posted.status_code == 201, posted.text
        await asyncio.sleep(0.05)

        alice_resp = await _end_stream(root_id, alice)
        carol_resp = await _end_stream(root_id, carol)
        bob_resp = await _end_stream(child.id, bob)
    finally:
        for task in (alice, carol, bob):
            task.cancel()
        await asyncio.gather(alice, carol, bob, return_exceptions=True)

    assert alice_resp.status_code == 200
    assert bob_resp.status_code == 200
    assert carol_resp.status_code == 200

    alice_frames = _human_chat_frames(alice_resp.text)
    bob_frames = _human_chat_frames(bob_resp.text)
    carol_frames = _human_chat_frames(carol_resp.text)
    assert len(alice_frames) == 1, alice_resp.text[:800]
    assert len(bob_frames) == 1, bob_resp.text[:800]
    assert carol_frames == []

    for frame, conversation_id in ((alice_frames[0], root_id), (bob_frames[0], child.id)):
        assert "body" not in frame
        assert frame["conversation_id"] == conversation_id
        assert frame["id"] == posted.json()["id"]
        parsed = _ADAPTER.validate_python(frame)
        assert parsed.type == "session.human_chat"
        assert not hasattr(parsed, "body") or "body" not in frame


def _single_user_app(db_uri: str, tmp_path: Path) -> FastAPI:
    """The ``app`` fixture's stores plus the chat store, and no permission store."""
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts-single"))
    return create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=SqlAlchemyFileStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        artifact_store=artifact_store,
        agent_cache=AgentCache(artifact_store=artifact_store, cache_dir=tmp_path / "cache-single"),
        comment_store=SqlAlchemyCommentStore(db_uri),
        chat_store=SqlAlchemySessionChatStore(db_uri),
    )


async def test_single_user_mode_and_shared_flag(
    auth_client: httpx.AsyncClient,
    runtime_init: None,
    db_uri: str,
    tmp_path: Path,
) -> None:
    """No permission store allows the chat. ``shared`` counts other people only."""
    solo_id = SqlAlchemyConversationStore(db_uri).create_conversation(title="solo").id
    transport = httpx.ASGITransport(app=_single_user_app(db_uri, tmp_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as solo:
        posted = await solo.post(f"/v1/sessions/{solo_id}/chat", json={"body": "just me"})
        assert posted.status_code == 201, posted.text
        listed = await solo.get(f"/v1/sessions/{solo_id}/chat")
        assert listed.status_code == 200, listed.text
        assert [row["body"] for row in listed.json()["items"]] == ["just me"]
        assert listed.json()["shared"] is False

    session_id = await _create_session(auth_client, "alice@example.com")
    _grant(db_uri, RESERVED_USER_PUBLIC, session_id)
    with_public = await _get(auth_client, session_id, "alice@example.com")
    assert with_public.status_code == 200, with_public.text
    assert with_public.json()["shared"] is False

    _grant(db_uri, "linked-alice@example.com", session_id)

    def _linked(sub: str) -> frozenset[str]:
        if sub == "alice@example.com":
            return frozenset({"linked-alice@example.com"})
        return frozenset()

    set_principal_resolver(_linked)
    try:
        own = await _get(auth_client, session_id, "alice@example.com")
        assert own.status_code == 200, own.text
        assert own.json()["shared"] is False

        _grant(db_uri, "bob@example.com", session_id)
        other = await _get(auth_client, session_id, "alice@example.com")
        assert other.status_code == 200, other.text
        assert other.json()["shared"] is True
    finally:
        set_principal_resolver(None)
