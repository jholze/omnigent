"""Parent wake when a child session returns, including REST-created children."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from omnigent.runner.subagent_work import subagent_wake_message_id
from omnigent.server.routes import sessions
from omnigent.server.routes._auth_helpers import SessionAccess
from omnigent.server.routes.sessions import routes_events
from omnigent.server.subagent_activity import record_subagent_activity
from omnigent.server.subagent_wake import (
    configure_subagent_return_notifier,
    flush_subagent_return_wakes,
    reset_subagent_return_wakes_for_tests,
    uninstall_return_wake_delivery,
)
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore

pytestmark = pytest.mark.asyncio


@dataclass
class _WakeRoute:
    """Mini sessions app plus the runner posts a wake actually forwarded."""

    store: SqlAlchemyConversationStore
    app: FastAPI
    client: httpx.AsyncClient
    forwarded: list[dict[str, Any]] = field(default_factory=list)


@pytest.fixture
async def wake_route(db_uri: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_WakeRoute]:
    """Sessions router whose runner client records event forwards and returns 202."""
    store = SqlAlchemyConversationStore(db_uri)
    app = FastAPI()
    app.include_router(
        sessions.create_sessions_router(store, SqlAlchemyAgentStore(db_uri)),
        prefix="/v1",
    )
    forwarded: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        forwarded.append(json.loads(request.content))
        return httpx.Response(202, json={"status": "accepted"})

    runner = httpx.AsyncClient(transport=httpx.MockTransport(capture), base_url="http://runner")

    async def get_runner_client(*_args: Any, **_kwargs: Any) -> httpx.AsyncClient:
        return runner

    monkeypatch.setattr(sessions, "_get_runner_client", get_runner_client)
    uninstall = configure_subagent_return_notifier(app, None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        route = _WakeRoute(store=store, app=app, client=client, forwarded=forwarded)
        try:
            yield route
        finally:
            await flush_subagent_return_wakes()
            uninstall()
            uninstall_return_wake_delivery()
            reset_subagent_return_wakes_for_tests()
            await runner.aclose()


def _user_messages(store: SqlAlchemyConversationStore, session_id: str) -> list[str]:
    page = store.list_items(session_id, order="asc", limit=50)
    texts: list[str] = []
    for item in page.data:
        if item.type != "message":
            continue
        content = item.data.content  # type: ignore[attr-defined]
        for block in content:
            if isinstance(block, dict) and block.get("type") == "input_text":
                texts.append(str(block.get("text")))
    return texts


async def test_rest_child_return_wakes_idle_parent(wake_route: _WakeRoute) -> None:
    """A REST child finishing posts one inbox notice and the runner accepts a turn."""
    parent = wake_route.store.create_conversation(title="parent")
    child = wake_route.store.create_conversation(
        kind="sub_agent",
        title="worker:investigate",
        parent_conversation_id=parent.id,
        sub_agent_name="worker",
    )
    await record_subagent_activity(
        child.id,
        "returned",
        wake_route.store,
        turn_id="resp_child_turn",
        status="completed",
    )
    await flush_subagent_return_wakes()

    assert len(wake_route.forwarded) == 1
    body = wake_route.forwarded[0]
    assert body["type"] == "message"
    notice = body["content"][0]["text"]
    assert notice.startswith("[System: sub-agent worker/")
    assert "finished (completed)" in notice
    assert "sys_read_inbox" in notice
    assert _user_messages(wake_route.store, parent.id) == [notice]

    await record_subagent_activity(
        child.id,
        "returned",
        wake_route.store,
        turn_id="resp_child_turn",
        status="completed",
    )
    await flush_subagent_return_wakes()
    assert len(wake_route.forwarded) == 1


async def test_tool_registered_child_wakes_parent_once(wake_route: _WakeRoute) -> None:
    """The runner wake and the returned-event wake share one dedupe key."""
    parent = wake_route.store.create_conversation(title="parent")
    child = wake_route.store.create_conversation(
        kind="sub_agent",
        title="researcher:auth",
        parent_conversation_id=parent.id,
        sub_agent_name="researcher",
        labels={"omnigent.subagent.dispatch_id": "subagent_abc"},
    )
    wake_key = subagent_wake_message_id(child.id, "resp_tool_turn")
    notice = (
        "[System: sub-agent researcher/auth finished (completed) — "
        "1 result waiting in inbox. Call sys_read_inbox to collect.]"
    )

    async def runner_wake() -> httpx.Response:
        return await wake_route.client.post(
            f"/v1/sessions/{parent.id}/events",
            json={
                "type": "message",
                "subagent_wake_key": wake_key,
                "data": {
                    "role": "user",
                    "stable_id": wake_key,
                    "content": [{"type": "input_text", "text": notice}],
                },
            },
        )

    await record_subagent_activity(
        child.id,
        "returned",
        wake_route.store,
        turn_id="resp_tool_turn",
        status="completed",
    )
    response, _flushed = await asyncio.gather(runner_wake(), flush_subagent_return_wakes())
    assert response.status_code == 202
    assert len(wake_route.forwarded) == 1
    assert _user_messages(wake_route.store, parent.id) == [notice]


async def test_codex_parent_is_not_woken(wake_route: _WakeRoute) -> None:
    """A codex-native parent consumes child threads itself and is not injected."""
    parent = wake_route.store.create_conversation(
        title="codex",
        harness_override="codex-native",
        labels={"omnigent.wrapper": "codex-native-ui"},
    )
    child = wake_route.store.create_conversation(
        kind="sub_agent",
        title="worker:side",
        parent_conversation_id=parent.id,
        sub_agent_name="worker",
    )
    await record_subagent_activity(
        child.id,
        "returned",
        wake_route.store,
        turn_id="resp_codex",
        status="completed",
    )
    await flush_subagent_return_wakes()
    assert wake_route.forwarded == []
    assert _user_messages(wake_route.store, parent.id) == []


async def test_return_wake_uses_parent_owner(
    wake_route: _WakeRoute, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The notice is posted as the parent owner, and skipped when there is none."""
    parent = wake_route.store.create_conversation(title="parent")
    child = wake_route.store.create_conversation(
        kind="sub_agent",
        title="worker:owned",
        parent_conversation_id=parent.id,
        sub_agent_name="worker",
    )

    async def allow(*_args: Any, **_kwargs: Any) -> SessionAccess:
        return SessionAccess(level=4, conversation=None)

    monkeypatch.setattr(routes_events, "_require_access_and_level", allow)
    monkeypatch.setattr(
        "omnigent.server.subagent_wake.get_session_owner_id",
        lambda *_args, **_kwargs: None,
    )
    uninstall = configure_subagent_return_notifier(wake_route.app, object())
    try:
        await record_subagent_activity(
            child.id,
            "returned",
            wake_route.store,
            turn_id="resp_unowned",
            status="completed",
        )
        await flush_subagent_return_wakes()
        assert wake_route.forwarded == []

        monkeypatch.setattr(
            "omnigent.server.subagent_wake.get_session_owner_id",
            lambda *_args, **_kwargs: "owner@example.com",
        )
        await record_subagent_activity(
            child.id,
            "returned",
            wake_route.store,
            turn_id="resp_owned",
            status="completed",
        )
        await flush_subagent_return_wakes()
    finally:
        uninstall()

    assert len(wake_route.forwarded) == 1
    page = wake_route.store.list_items(parent.id, order="asc", limit=20)
    messages = [item for item in page.data if item.type == "message"]
    assert len(messages) == 1
    assert messages[0].created_by == "owner@example.com"
