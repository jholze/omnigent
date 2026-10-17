"""Team-chat frames on ``WS /v1/sessions/updates``.

A stored message is announced to each member's updates socket as a
``chat`` frame that carries ids and times but never the message body.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.runtime import user_session_stream
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.app import create_app
from omnigent.server.auth import RESERVED_USER_LOCAL, UnifiedAuthProvider
from omnigent.server.chat_realtime import publish_chat
from omnigent.server.feature_flags import FEATURES_ENV_VAR
from omnigent.server.routes._sessions.helpers import _discovery_key
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore

ANNA = "anna@bloopark.de"
JENS = "jens@bloopark.de"
BOB = "bob@bloopark.de"
CARLA = "carla@bloopark.de"

_FRAME_KEYS = {
    "type",
    "kind",
    "channel_id",
    "message_id",
    "thread_root_id",
    "created_at",
    "author_id",
}


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


def _build_no_auth_app(db_uri: str, tmp_path: Path) -> FastAPI:
    # Single-user mode: no permission store and no auth provider, so the chat
    # caller resolves to ``RESERVED_USER_LOCAL`` and the updates socket
    # subscribes under the shared discovery key.
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    return create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=SqlAlchemyFileStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        artifact_store=artifact_store,
        agent_cache=AgentCache(artifact_store=artifact_store, cache_dir=tmp_path / "cache"),
    )


@pytest.fixture()
def no_auth_chat_app(
    db_uri: str, runtime_init: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Client against a single-user (no auth) app built with ``team_chat`` on."""
    monkeypatch.setenv(FEATURES_ENV_VAR, "team_chat")
    yield TestClient(_build_no_auth_app(db_uri, tmp_path))


@pytest.fixture()
def chat_app(
    db_uri: str, runtime_init: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Client against an app built with ``team_chat`` on."""
    monkeypatch.setenv(FEATURES_ENV_VAR, "team_chat")
    yield TestClient(_build_app(db_uri, tmp_path))


@pytest.fixture()
def flag_off_app(
    db_uri: str, runtime_init: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Client against an app built with ``OMNIGENT_FEATURES`` unset."""
    monkeypatch.delenv(FEATURES_ENV_VAR, raising=False)
    yield TestClient(_build_app(db_uri, tmp_path))


def _wait_for_subscriber(user: str, *, timeout_s: float = 5.0) -> None:
    """Block until ``user``'s updates socket has subscribed to its stream."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if user_session_stream._subscribers.get(user):
            return
        time.sleep(0.01)
    raise AssertionError(f"no updates subscriber for {user}")


def _wait_for_shared_subscriber(*, timeout_s: float = 5.0) -> None:
    """Block until the shared single-user updates socket has subscribed."""
    key = _discovery_key(None)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if user_session_stream._subscribers.get(key):
            return
        time.sleep(0.01)
    raise AssertionError(f"no updates subscriber for {key}")


def _recv_raw(ws: Any, wanted: set[str], *, max_frames: int = 50) -> str:
    """Return the raw text of the first frame whose ``type`` is in ``wanted``."""
    for _ in range(max_frames):
        raw = ws.receive_text()
        if json.loads(raw).get("type") in wanted:
            return raw
    raise AssertionError(f"no frame in {wanted} after {max_frames} frames")


def _bootstrap(client: TestClient, user: str) -> dict[str, Any]:
    resp = client.get("/v1/chat/bootstrap", headers=_headers(user))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _general_id(client: TestClient, user: str) -> str:
    (general,) = (c for c in _bootstrap(client, user)["channels"] if c["name"] == "general")
    return general["id"]


def _post(client: TestClient, user: str, channel_id: str, body: str) -> dict[str, Any]:
    resp = client.post(
        f"/v1/chat/channels/{channel_id}/messages",
        json={"body": body},
        headers=_headers(user),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _billing_with(client: TestClient, members: list[str]) -> str:
    """Create ``#billing`` as Anna and join each of ``members``."""
    resp = client.post("/v1/chat/channels", json={"name": "billing"}, headers=_headers(ANNA))
    assert resp.status_code == 201, resp.text
    billing_id = resp.json()["id"]
    for member in members:
        joined = client.post(f"/v1/chat/channels/{billing_id}/join", headers=_headers(member))
        assert joined.status_code == 204, joined.text
    return billing_id


def test_ac_013a_member_socket_gets_frame_with_author_and_time(chat_app: TestClient) -> None:
    general_id = _general_id(chat_app, ANNA)
    _bootstrap(chat_app, JENS)

    with chat_app.websocket_connect("/v1/sessions/updates", headers=_headers(JENS)) as ws:
        _wait_for_subscriber(JENS)
        message = _post(chat_app, ANNA, general_id, "Deploy heute 16 Uhr?")
        frame = json.loads(_recv_raw(ws, {"chat"}))

    assert frame["kind"] == "message.created"
    assert frame["message_id"] == message["id"]
    assert frame["author_id"] == ANNA
    assert frame["created_at"] == message["created_at"]


def test_ac_014a_every_member_socket_gets_bodyless_frame(chat_app: TestClient) -> None:
    billing_id = _billing_with(chat_app, [JENS, BOB])
    marker = f"marker-{time.monotonic_ns()}"

    with (
        chat_app.websocket_connect("/v1/sessions/updates", headers=_headers(JENS)) as jens_ws,
        chat_app.websocket_connect("/v1/sessions/updates", headers=_headers(BOB)) as bob_ws,
    ):
        _wait_for_subscriber(JENS)
        _wait_for_subscriber(BOB)
        message = _post(chat_app, ANNA, billing_id, marker)
        raws = [_recv_raw(jens_ws, {"chat"}), _recv_raw(bob_ws, {"chat"})]

    for raw in raws:
        assert marker not in raw
        frame = json.loads(raw)
        assert set(frame) == _FRAME_KEYS
        assert frame["type"] == "chat"
        assert frame["kind"] == "message.created"
        assert frame["channel_id"] == billing_id
        assert frame["message_id"] == message["id"]
        assert frame["thread_root_id"] is None
        assert frame["created_at"] == message["created_at"]
        assert frame["author_id"] == ANNA


def test_ac_014b_non_member_socket_gets_no_frame(chat_app: TestClient) -> None:
    billing_id = _billing_with(chat_app, [JENS])
    general_id = _general_id(chat_app, ANNA)
    _bootstrap(chat_app, CARLA)

    with chat_app.websocket_connect("/v1/sessions/updates", headers=_headers(CARLA)) as ws:
        _wait_for_subscriber(CARLA)
        _post(chat_app, ANNA, billing_id, "nur fuer billing")
        # Carla is in #general, so this later post is the first frame she may get.
        sentinel = _post(chat_app, ANNA, general_id, "an alle")
        frame = json.loads(_recv_raw(ws, {"chat"}))

    assert frame["channel_id"] == general_id
    assert frame["message_id"] == sentinel["id"]


def test_ac_014_flag_off_socket_drops_chat_frame(flag_off_app: TestClient) -> None:
    with flag_off_app.websocket_connect("/v1/sessions/updates", headers=_headers(ANNA)) as ws:
        _wait_for_subscriber(ANNA)
        publish_chat(
            [ANNA],
            {
                "kind": "message.created",
                "channel_id": "c" * 32,
                "message_id": "m" * 32,
                "thread_root_id": None,
                "created_at": 1,
                "author_id": JENS,
            },
        )
        # Events reach one subscriber in publish order, so this proves the
        # chat event before it was dropped rather than still in flight.
        user_session_stream.publish(ANNA, {"type": "hosts_changed"})
        frame = json.loads(_recv_raw(ws, {"chat", "hosts_changed"}))

    assert frame["type"] == "hosts_changed"


def test_ac_014_no_auth_member_socket_gets_bodyless_frame(no_auth_chat_app: TestClient) -> None:
    """In single-user mode the ``local`` member's frame reaches the shared
    updates socket, not a ``"local"`` channel nobody subscribes to (FR-014)."""
    bootstrap = no_auth_chat_app.get("/v1/chat/bootstrap")
    assert bootstrap.status_code == 200, bootstrap.text
    (general,) = (c for c in bootstrap.json()["channels"] if c["name"] == "general")

    with no_auth_chat_app.websocket_connect("/v1/sessions/updates") as ws:
        _wait_for_shared_subscriber()
        posted = no_auth_chat_app.post(
            f"/v1/chat/channels/{general['id']}/messages",
            json={"body": "Deploy heute 16 Uhr?"},
        )
        assert posted.status_code == 201, posted.text
        frame = json.loads(_recv_raw(ws, {"chat"}))

    assert frame["kind"] == "message.created"
    assert frame["channel_id"] == general["id"]
    assert frame["message_id"] == posted.json()["id"]
    assert frame["author_id"] == RESERVED_USER_LOCAL
    assert "body" not in frame
