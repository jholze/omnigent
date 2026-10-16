"""HTTP tests for the native ``POST /mcp`` endpoint.

Covers the JSON-RPC handshake, accounts-mode bearer auth, dry-run writes,
compact session rows, and a confirmed create + send against a real app
instance (in-process ASGI, the same server the rest of this suite drives).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from omnigent.db.utils import get_or_create_engine
from omnigent.runtime import init as init_runtime
from omnigent.runtime import telemetry
from omnigent.runtime.agent_cache import AgentCache
from omnigent.runtime.caps import RuntimeCaps
from omnigent.server.accounts_store import SqlAlchemyAccountStore
from omnigent.server.app import create_app
from omnigent.server.auth import UnifiedAuthProvider, create_auth_provider
from omnigent.server.mcp_endpoint import (
    ToolFailure,
    _forwarded_headers,
    _TurnState,
    _watch_posted,
    mcp_endpoint_enabled,
    working_directory_from_labels,
)
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.comment_store.sqlalchemy_store import SqlAlchemyCommentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from omnigent.stores.policy_store.sqlalchemy_store import SqlAlchemyPolicyStore
from omnigent.stores.project_store.sqlalchemy_store import SqlAlchemyProjectStore
from tests.server.conftest import _app_client
from tests.server.helpers import create_test_agent

_ACCEPT = {"accept": "application/json, text/event-stream"}


def _rpc_body(
    method: str, params: dict[str, Any] | None = None, *, rpc_id: int | None = 1
) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if rpc_id is not None:
        body["id"] = rpc_id
    if params is not None:
        body["params"] = params
    return body


def _tool_text(response: httpx.Response) -> Any:
    payload = response.json()
    assert "error" not in payload, payload
    result = payload["result"]
    assert result.get("isError") is not True, result
    return json.loads(result["content"][0]["text"])


def _composer_labels(path: str) -> dict[str, str]:
    raw = json.dumps(
        {
            "version": 1,
            "working_directory": {"path": path},
            "worktree": {"mode": "none"},
        }
    )
    labels: dict[str, str] = {}
    for index, offset in enumerate(range(0, len(raw), 240)):
        labels[f"omnigent.composer_context.v1.{index}"] = raw[offset : offset + 240]
    return labels


# ── pure helpers ────────────────────────────────────────────────


def test_mcp_enabled_env_overrides_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """``OMNIGENT_MCP_ENABLED`` wins over the server-config key; default is on."""
    monkeypatch.delenv("OMNIGENT_MCP_ENABLED", raising=False)
    assert mcp_endpoint_enabled({}) is True
    assert mcp_endpoint_enabled({"mcp_enabled": False}) is False
    monkeypatch.setenv("OMNIGENT_MCP_ENABLED", "0")
    assert mcp_endpoint_enabled({"mcp_enabled": True}) is False
    monkeypatch.setenv("OMNIGENT_MCP_ENABLED", "yes")
    assert mcp_endpoint_enabled({"mcp_enabled": False}) is True
    monkeypatch.setenv("OMNIGENT_MCP_ENABLED", "on")
    assert mcp_endpoint_enabled({"mcp_enabled": False}) is True
    monkeypatch.setenv("OMNIGENT_MCP_ENABLED", "off")
    assert mcp_endpoint_enabled({"mcp_enabled": True}) is False
    monkeypatch.setenv("OMNIGENT_MCP_ENABLED", "maybe")
    assert mcp_endpoint_enabled({"mcp_enabled": True}) is False
    monkeypatch.delenv("OMNIGENT_MCP_ENABLED", raising=False)
    assert mcp_endpoint_enabled({"mcp_enabled": "on"}) is True
    assert mcp_endpoint_enabled({"mcp_enabled": "OFF"}) is False
    assert mcp_endpoint_enabled({"mcp_enabled": "maybe"}) is False


def test_working_directory_joins_composer_label_chunks() -> None:
    """The ``.0`` chunk (and any continuation) is the composer working directory."""
    path = "/work/" + ("a" * 300)
    labels = _composer_labels(path)
    assert "omnigent.composer_context.v1.0" in labels
    assert working_directory_from_labels(labels) == path
    assert working_directory_from_labels({}) is None
    assert working_directory_from_labels({"omnigent.composer_context.v1.0": "not-json"}) is None


def test_turn_state_ignores_stale_idle_and_stops_on_elicitation() -> None:
    """A previous idle is not this turn; an elicitation ends the wait as needs_input."""
    state = _TurnState()
    state.observe({"type": "session.status", "status": "idle"})
    assert state.done is False
    state.observe({"type": "session.status", "status": "running", "response_id": "resp_1"})
    state.observe(
        {
            "type": "response.elicitation_request",
            "elicitation_id": "elicit_1",
            "params": {"message": "Allow?"},
        }
    )
    assert state.done is True
    assert state.needs_input == {"elicitation_id": "elicit_1", "message": "Allow?"}


def test_turn_state_records_response_failed() -> None:
    """A harness failure on the live stream is the turn's error, not a transport error."""
    state = _TurnState()
    state.observe({"type": "response.output_text.delta", "delta": "partial", "message_id": "m1"})
    state.observe({"type": "response.failed", "response": {"error": {"message": "Not logged in"}}})
    result = state.to_result({"ok": True}, False)
    assert result["status"] == "failed"
    assert result["error"] == "Not logged in"
    assert result["reply"] == "partial"
    assert result["end_reason"] == "response.failed"


# ── app fixtures ────────────────────────────────────────────────


@pytest.fixture()
def mcp_app(
    runtime_init: None, db_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> FastAPI:
    """Real server app with projects, no auth provider, MCP on."""
    del runtime_init
    monkeypatch.delenv("OMNIGENT_MCP_ENABLED", raising=False)
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    return create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=SqlAlchemyFileStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        artifact_store=artifact_store,
        agent_cache=AgentCache(artifact_store=artifact_store, cache_dir=tmp_path / "cache"),
        comment_store=SqlAlchemyCommentStore(db_uri),
        project_store=SqlAlchemyProjectStore(db_uri),
        policy_store=SqlAlchemyPolicyStore(db_uri),
        host_store=HostStore(db_uri),
        server_config={},
    )


@pytest_asyncio.fixture()
async def mcp_client(mcp_app: FastAPI, mock_llm: Any, tmp_path: Path) -> httpx.AsyncClient:
    async with _app_client(mcp_app, mock_llm, tmp_path) as client:
        yield client


def _sync_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    auth_provider: Any,
    server_config: dict[str, Any],
    accounts: bool = False,
) -> FastAPI:
    """Build a server app for auth and flag tests. Isolated data dir, no mock LLM."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / ".omnigent"))
    monkeypatch.delenv("OMNIGENT_MCP_ENABLED", raising=False)
    db_url = f"sqlite:///{tmp_path}/mcp.db"
    get_or_create_engine(db_url)
    telemetry.init()
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    agent_cache = AgentCache(artifact_store=artifact_store, cache_dir=tmp_path / "cache")
    init_runtime(
        agent_cache=agent_cache,
        caps=RuntimeCaps(),
        agent_store=SqlAlchemyAgentStore(db_url),
        file_store=SqlAlchemyFileStore(db_url),
        conversation_store=SqlAlchemyConversationStore(db_url),
        artifact_store=artifact_store,
        comment_store=SqlAlchemyCommentStore(db_url),
    )
    account_store = SqlAlchemyAccountStore(db_url) if accounts else None
    permission_store = SqlAlchemyPermissionStore(db_url) if auth_provider is not None else None
    return create_app(
        agent_store=SqlAlchemyAgentStore(db_url),
        file_store=SqlAlchemyFileStore(db_url),
        conversation_store=SqlAlchemyConversationStore(db_url),
        artifact_store=artifact_store,
        agent_cache=agent_cache,
        comment_store=SqlAlchemyCommentStore(db_url),
        permission_store=permission_store,
        auth_provider=auth_provider,
        account_store=account_store,
        project_store=SqlAlchemyProjectStore(db_url),
        server_config=server_config,
    )


# ── JSON-RPC over HTTP ──────────────────────────────────────────


async def test_initialize_tools_list_and_health(mcp_client: httpx.AsyncClient) -> None:
    """Handshake, tool catalog, and a read call succeed without a token when auth is off."""
    init = await mcp_client.post(
        "/mcp", json=_rpc_body("initialize", {"protocolVersion": "2025-03-26"})
    )
    assert init.status_code == 200, init.text
    body = init.json()
    assert body["result"]["protocolVersion"] == "2025-03-26"
    assert body["result"]["serverInfo"]["name"] == "omnigent"
    assert init.headers["mcp-protocol-version"] == "2025-03-26"

    listed = await mcp_client.post("/mcp", json=_rpc_body("tools/list"))
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert {
        "health",
        "info",
        "list_agents",
        "list_hosts",
        "list_harnesses",
        "list_projects",
        "list_sessions",
        "get_session",
        "get_session_items",
        "create_session",
        "send_message",
        "interrupt_turn",
        "stop_session",
    } <= names

    health = await mcp_client.post(
        "/mcp",
        json=_rpc_body("tools/call", {"name": "health"}),
        headers=_ACCEPT,
    )
    assert health.status_code == 200, health.text
    assert _tool_text(health) == {"status": "ok"}

    info = await mcp_client.post(
        "/mcp", json=_rpc_body("tools/call", {"name": "info", "arguments": {}})
    )
    assert _tool_text(info)["accounts_enabled"] is False


async def test_notifications_unknown_method_and_parse_error(mcp_client: httpx.AsyncClient) -> None:
    """Notifications get 202, unknown methods are JSON-RPC errors, bad JSON is 400."""
    note = await mcp_client.post(
        "/mcp",
        json=_rpc_body("notifications/initialized", rpc_id=None),
    )
    assert note.status_code == 202
    assert note.content == b""

    unknown = await mcp_client.post("/mcp", json=_rpc_body("nope"))
    assert unknown.status_code == 200
    assert unknown.json()["error"]["code"] == -32601

    parsed = await mcp_client.post(
        "/mcp", content=b"not-json", headers={"content-type": "application/json"}
    )
    assert parsed.status_code == 400
    assert parsed.json()["error"]["code"] == -32700

    get = await mcp_client.get("/mcp")
    assert get.status_code == 405

    ping = await mcp_client.post("/mcp", json=_rpc_body("ping"))
    assert ping.status_code == 200, ping.text
    assert ping.json()["result"] == {}

    resources = await mcp_client.post("/mcp", json=_rpc_body("resources/list"))
    assert resources.json()["result"] == {"resources": []}
    prompts = await mcp_client.post("/mcp", json=_rpc_body("prompts/list"))
    assert prompts.json()["result"] == {"prompts": []}

    invalid = await mcp_client.post("/mcp", json={"jsonrpc": "1.0", "method": "ping", "id": 4})
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == -32600


async def test_dry_run_writes_change_nothing(mcp_client: httpx.AsyncClient) -> None:
    """create, send, interrupt, and stop return the planned request and do not call it."""
    agent = await create_test_agent(mcp_client, name="dry-run-agent")
    before = (await mcp_client.get("/v1/sessions?visibility=all")).json()
    before_ids = {row["id"] for row in before["data"]}

    created = await mcp_client.post(
        "/mcp",
        json=_rpc_body(
            "tools/call",
            {
                "name": "create_session",
                "arguments": {
                    "agent_name": "dry-run-agent",
                    "title": "not created",
                    "message": "do not send",
                    "confirm": "true",
                },
            },
        ),
    )
    planned = _tool_text(created)
    assert planned["dry_run"] is True
    assert planned["method"] == "POST"
    assert planned["path"] == "/v1/sessions"
    assert planned["body"]["agent_id"] == agent["id"]
    assert planned["body"]["initial_items"][0]["data"]["content"][0]["text"] == "do not send"

    session_id = next(iter(before_ids))
    for name, event_type in (("interrupt_turn", "interrupt"), ("stop_session", "stop_session")):
        response = await mcp_client.post(
            "/mcp",
            json=_rpc_body(
                "tools/call",
                {"name": name, "arguments": {"session_id": session_id}},
            ),
        )
        body = _tool_text(response)
        assert body["dry_run"] is True
        assert body["body"] == {"type": event_type, "data": {}}

    sent = await mcp_client.post(
        "/mcp",
        json=_rpc_body(
            "tools/call",
            {
                "name": "send_message",
                "arguments": {"session_id": session_id, "text": "stay dry", "wait": True},
            },
        ),
    )
    preview = _tool_text(sent)
    assert preview["dry_run"] is True
    assert preview["wait"]["stream"].endswith("/stream?idle=false")
    assert preview["body"]["type"] == "message"

    after = (await mcp_client.get("/v1/sessions?visibility=all")).json()
    assert {row["id"] for row in after["data"]} == before_ids
    missing = await mcp_client.post(
        "/mcp",
        json=_rpc_body(
            "tools/call",
            {
                "name": "create_session",
                "arguments": {"agent_name": "nobody-home", "confirm": True},
            },
        ),
    )
    assert missing.json()["result"]["isError"] is True
    assert "agent_name" in missing.json()["result"]["content"][0]["text"]


async def test_list_projects_and_compact_sessions(mcp_client: httpx.AsyncClient) -> None:
    """Projects return id/name/workspace/agent, and session rows stay compact and filterable."""
    agent = await create_test_agent(mcp_client, name="project-agent")
    created = await mcp_client.post(
        "/v1/projects",
        json={
            "name": "Alpha",
            "config": {"workspace": "/work/alpha", "agentId": agent["id"]},
        },
    )
    assert created.status_code == 200, created.text
    project_id = created.json()["id"]
    other = await mcp_client.post("/v1/projects", json={"name": "Beta", "config": {}})
    assert other.status_code == 200, other.text

    projects = await mcp_client.post(
        "/mcp", json=_rpc_body("tools/call", {"name": "list_projects"})
    )
    rows = _tool_text(projects)["data"]
    alpha = next(row for row in rows if row["id"] == project_id)
    assert alpha == {
        "id": project_id,
        "name": "Alpha",
        "workspace": "/work/alpha",
        "default_agent": agent["id"],
    }
    assert set(alpha) == {"id", "name", "workspace", "default_agent"}

    session = await mcp_client.post(
        "/v1/sessions",
        json={
            "agent_id": agent["id"],
            "title": "filed chat",
            "labels": _composer_labels("/work/alpha"),
        },
    )
    assert session.status_code == 201, session.text
    session_id = session.json()["id"]
    filed = await mcp_client.patch(f"/v1/sessions/{session_id}", json={"project_id": project_id})
    assert filed.status_code == 200, filed.text

    listed = await mcp_client.post(
        "/mcp",
        json=_rpc_body(
            "tools/call",
            {"name": "list_sessions", "arguments": {"project_id": project_id, "limit": 20}},
        ),
    )
    page = _tool_text(listed)
    assert [row["id"] for row in page["data"]] == [session_id]
    row = page["data"][0]
    assert row["title"] == "filed chat"
    assert row["agent_name"] == "project-agent"
    assert row["project_id"] == project_id
    assert row["working_directory"] == "/work/alpha"
    assert "updated_at" in row
    assert "labels" not in row
    assert "omnigent.composer_context" not in json.dumps(row)

    by_name = await mcp_client.post(
        "/mcp",
        json=_rpc_body(
            "tools/call",
            {"name": "list_sessions", "arguments": {"project": "Alpha"}},
        ),
    )
    assert [item["id"] for item in _tool_text(by_name)["data"]] == [session_id]


async def test_get_session_omits_items_and_items_are_truncated(
    mcp_client: httpx.AsyncClient,
) -> None:
    """get_session drops the transcript; get_session_items keeps the newest page and clips text."""
    agent = await create_test_agent(mcp_client, name="items-agent")
    long_text = "x" * 500
    session = await mcp_client.post(
        "/v1/sessions",
        json={
            "agent_id": agent["id"],
            "initial_items": [
                {
                    "type": "message",
                    "data": {
                        "role": "user",
                        "content": [{"type": "input_text", "text": long_text}],
                    },
                }
            ],
        },
    )
    assert session.status_code == 201, session.text
    session_id = session.json()["id"]

    fetched = await mcp_client.post(
        "/mcp",
        json=_rpc_body(
            "tools/call", {"name": "get_session", "arguments": {"session_id": session_id}}
        ),
    )
    body = _tool_text(fetched)
    assert body["items_omitted"] is True
    assert body["item_count"] >= 1
    assert "items" not in body

    items = await mcp_client.post(
        "/mcp",
        json=_rpc_body(
            "tools/call",
            {
                "name": "get_session_items",
                "arguments": {"session_id": session_id, "limit": 30, "max_chars": 200},
            },
        ),
    )
    page = _tool_text(items)
    assert len(page["data"]) <= 30
    block = page["data"][0]["content"][0]
    assert block["truncated"] is True
    assert block["text"].endswith("…[truncated]")
    assert long_text not in block["text"]


async def _bind_local_runner(
    app: FastAPI, session_id: str
) -> tuple[Any, asyncio.Task[None], httpx.AsyncClient, Any, Path]:
    """Point one session at a co-located runner so a confirmed send is dispatched.

    The server refuses a message when no runner is bound. This starts the real
    runner app on localhost and stamps ``runner_id`` so the events route
    forwards the message and relays the harness stream. The harness socket
    lives under a short ``/tmp`` parent: pytest's temp dir exceeds the
    AF_UNIX path limit.
    """
    import socket
    import uuid

    import uvicorn

    from omnigent.runner.app import create_runner_app
    from omnigent.runtime import get_agent_cache, get_conversation_store
    from omnigent.runtime.harnesses.process_manager import HarnessProcessManager
    from omnigent.server.routes import sessions as sessions_module
    from tests.runner.helpers import NullServerClient

    assert get_conversation_store().set_runner_id(session_id, "runner_mcp_test")

    async def resolve_spec(agent_id: str, bound_session_id: str | None = None) -> Any:
        del bound_session_id
        record = app.state.agent_store.get(agent_id)
        if record is None or not record.bundle_location:
            return None
        loaded = get_agent_cache().load(agent_id, record.bundle_location)
        return loaded.spec

    short_parent = Path(f"/tmp/oa-mcp-{uuid.uuid4().hex[:8]}")
    short_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    process_manager = HarnessProcessManager(tmp_parent=short_parent)
    await process_manager.start()

    runner_app = create_runner_app(
        process_manager=process_manager,
        spec_resolver=resolve_spec,
        server_client=NullServerClient(),  # type: ignore[arg-type]
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(runner_app, host="127.0.0.1", port=port, log_level="warning")
    )
    server_task = asyncio.create_task(server.serve())
    for _ in range(50):
        if server.started:
            break
        await asyncio.sleep(0.05)
    else:
        server.should_exit = True
        raise AssertionError("local runner did not start")

    runner_http = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30.0)

    async def bound_runner(*_args: Any, **_kwargs: Any) -> httpx.AsyncClient:
        return runner_http

    sessions_module._get_runner_client = bound_runner  # type: ignore[method-assign]
    return server, server_task, runner_http, process_manager, short_parent


async def test_confirmed_create_and_send(
    mcp_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A confirmed create and send are dispatched to a co-located runner.

    The runner spawns the session's claude-sdk harness in a subprocess. That
    process does not see the in-process mock LLM, so the turn ends failed
    when the harness cannot log in or start. A reply is also accepted.
    """
    from omnigent.server.routes import sessions as sessions_module

    original_get_runner = sessions_module._get_runner_client
    await create_test_agent(mcp_client, name="turn-agent")
    created = await mcp_client.post(
        "/mcp",
        json=_rpc_body(
            "tools/call",
            {
                "name": "create_session",
                "arguments": {
                    "agent_name": "turn-agent",
                    "title": "mcp turn",
                    "confirm": True,
                },
            },
        ),
    )
    session = _tool_text(created)["session"]
    session_id = session["id"]
    assert session["items_omitted"] is True
    app = mcp_client._transport.app  # type: ignore[attr-defined]
    server, server_task, runner_http, process_manager, short_parent = await _bind_local_runner(
        app, session_id
    )

    try:
        sent = await mcp_client.post(
            "/mcp",
            json=_rpc_body(
                "tools/call",
                {
                    "name": "send_message",
                    "arguments": {
                        "session_id": session_id,
                        "text": "ping from mcp",
                        "confirm": True,
                        "wait": True,
                        "timeout_seconds": 45,
                    },
                },
            ),
        )
        assert sent.status_code == 200, sent.text
        envelope = sent.json()["result"]
        assert envelope.get("isError") is not True, envelope
        outcome = json.loads(envelope["content"][0]["text"])
        print(
            "MCP_TURN",
            json.dumps(
                {
                    "status": outcome.get("status"),
                    "end_reason": outcome.get("end_reason"),
                    "reply_source": outcome.get("reply_source"),
                    "timed_out": outcome.get("timed_out"),
                    "event_count": outcome.get("event_count"),
                    "error": outcome.get("error"),
                    "reply": outcome.get("reply"),
                    "needs_input": outcome.get("needs_input"),
                }
            ),
        )

        items = await mcp_client.get(f"/v1/sessions/{session_id}/items?limit=30&order=asc")
        assert items.status_code == 200, items.text
        transcript = json.dumps(items.json())
        assert "ping from mcp" in transcript
        reached_harness = (
            bool(outcome.get("reply"))
            or bool(outcome.get("error"))
            or outcome.get("status") in {"idle", "failed"}
        )
        assert reached_harness, outcome
        if outcome.get("reply"):
            assert outcome["reply"]
    finally:
        sessions_module._get_runner_client = original_get_runner  # type: ignore[method-assign]
        await runner_http.aclose()
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=5)
        await process_manager.shutdown()
        import shutil

        shutil.rmtree(short_parent, ignore_errors=True)


_FLUSH = object()


class _ScriptedStream:
    """Runner SSE stream: a ready heartbeat, then frames the test enqueues."""

    def __init__(self, runner: _ScriptedRunner) -> None:
        self._runner = runner

    async def __aenter__(self) -> _ScriptedStream:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_text(self) -> Any:
        yield 'data: {"type": "session.heartbeat"}\n\n'
        while True:
            item = await self._runner.queue.get()
            if item is None:
                return
            if item is _FLUSH:
                self._runner.flushed.set()
                continue
            yield str(item)


class _ScriptedResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://runner.local")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("scripted", request=request, response=response)


class _ScriptedRunner:
    """In-process runner. A user message emits scripted SSE once the relay is reading."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.posts: list[tuple[str, Any]] = []
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self.flushed = asyncio.Event()

    def stream(self, method: str, path: str, **_kwargs: Any) -> _ScriptedStream:
        del method, path
        return _ScriptedStream(self)

    async def post(self, path: str, **kwargs: Any) -> _ScriptedResponse:
        body = kwargs.get("json")
        self.posts.append((path, body))
        if (
            str(path).endswith("/events")
            and isinstance(body, dict)
            and body.get("type") == "message"
        ):
            if self.mode == "reply":
                delta = {
                    "type": "response.output_text.delta",
                    "delta": "Hello from the test agent.",
                    "message_id": "m1",
                }
                await self._emit(
                    [
                        f"data: {json.dumps(delta)}\n\n",
                        'data: {"type":"session.status","status":"idle"}\n\n',
                    ]
                )
            elif self.mode == "timeout":
                await self._emit(['data: {"type":"session.status","status":"running"}\n\n'])
            elif self.mode == "elicitation":
                await self._emit(
                    [
                        "data: "
                        + json.dumps(
                            {
                                "type": "response.elicitation_request",
                                "elicitation_id": "elicit_scripted",
                                "params": {"message": "Need a choice"},
                            }
                        )
                        + "\n\n"
                    ]
                )
        if path == "/v1/sessions":
            return _ScriptedResponse(200, {"ok": True})
        return _ScriptedResponse(202, {"queued": True})

    async def _emit(self, frames: list[str]) -> None:
        for frame in frames:
            await self.queue.put(frame)
        self.flushed.clear()
        await self.queue.put(_FLUSH)
        await asyncio.wait_for(self.flushed.wait(), timeout=5)

    async def aclose(self) -> None:
        await self.queue.put(None)


async def _confirmed_session(client: httpx.AsyncClient, agent_name: str) -> str:
    await create_test_agent(client, name=agent_name)
    created = await client.post(
        "/mcp",
        json=_rpc_body(
            "tools/call",
            {
                "name": "create_session",
                "arguments": {"agent_name": agent_name, "title": agent_name, "confirm": True},
            },
        ),
    )
    return str(_tool_text(created)["session"]["id"])


async def _bind_scripted(session_id: str, mode: str) -> tuple[Any, Any, _ScriptedRunner]:
    from omnigent.runtime import get_conversation_store
    from omnigent.server.routes import sessions as sessions_module

    assert get_conversation_store().set_runner_id(session_id, "runner_mcp_scripted")
    runner = _ScriptedRunner(mode)
    original = sessions_module._get_runner_client

    async def bound(*_args: Any, **_kwargs: Any) -> _ScriptedRunner:
        return runner

    sessions_module._get_runner_client = bound  # type: ignore[method-assign]
    return sessions_module, original, runner


async def _unbind_scripted(sessions_module: Any, original: Any, runner: _ScriptedRunner) -> None:
    sessions_module._get_runner_client = original  # type: ignore[method-assign]
    handles = list(sessions_module._runner_relay_tasks.values())
    sessions_module._runner_relay_tasks.clear()
    for handle in handles:
        handle.task.cancel()
    for handle in handles:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(handle.task, timeout=2)
    await runner.aclose()


async def _send(client: httpx.AsyncClient, session_id: str, **arguments: Any) -> dict[str, Any]:
    payload = {"session_id": session_id, "text": "ping from mcp", "confirm": True, "wait": True}
    payload.update(arguments)
    sent = await client.post(
        "/mcp",
        json=_rpc_body("tools/call", {"name": "send_message", "arguments": payload}),
    )
    assert sent.status_code == 200, sent.text
    envelope = sent.json()["result"]
    assert envelope.get("isError") is not True, envelope
    outcome = json.loads(envelope["content"][0]["text"])
    print(
        "MCP_TURN",
        json.dumps(
            {
                key: outcome.get(key)
                for key in (
                    "status",
                    "end_reason",
                    "reply_source",
                    "timed_out",
                    "event_count",
                    "error",
                    "reply",
                    "needs_input",
                )
            }
        ),
    )
    return outcome


async def test_scripted_wait_receives_reply_and_items(mcp_client: httpx.AsyncClient) -> None:
    """create_session then send_message wait returns the runner's streamed reply."""
    session_id = await _confirmed_session(mcp_client, "reply-agent")
    sessions_module, original, runner = await _bind_scripted(session_id, "reply")
    try:
        outcome = await _send(mcp_client, session_id, timeout_seconds=15)
        assert outcome["reply"] == "Hello from the test agent."
        assert outcome["timed_out"] is False
        assert outcome["end_reason"] == "idle"
        assert outcome["needs_input"] is None
        items = await mcp_client.post(
            "/mcp",
            json=_rpc_body(
                "tools/call",
                {
                    "name": "get_session_items",
                    "arguments": {"session_id": session_id, "order": "asc"},
                },
            ),
        )
        page = _tool_text(items)
        transcript = json.dumps(page)
        assert "ping from mcp" in transcript
    finally:
        await _unbind_scripted(sessions_module, original, runner)


async def test_scripted_wait_times_out(mcp_client: httpx.AsyncClient) -> None:
    """A turn that stays running ends as timed_out and keeps the user message."""
    session_id = await _confirmed_session(mcp_client, "timeout-agent")
    sessions_module, original, runner = await _bind_scripted(session_id, "timeout")
    try:
        outcome = await _send(mcp_client, session_id, timeout_seconds=1)
        assert outcome["timed_out"] is True
        assert outcome["end_reason"] == "timeout"
        assert outcome["status"] == "running"
        items = await mcp_client.get(f"/v1/sessions/{session_id}/items?limit=30&order=asc")
        assert "ping from mcp" in items.text
    finally:
        await _unbind_scripted(sessions_module, original, runner)


async def test_scripted_wait_elicitation_needs_input(mcp_client: httpx.AsyncClient) -> None:
    """An elicitation on the live stream ends the wait as needs_input."""
    session_id = await _confirmed_session(mcp_client, "elicit-agent")
    sessions_module, original, runner = await _bind_scripted(session_id, "elicitation")
    try:
        outcome = await _send(mcp_client, session_id, timeout_seconds=15)
        assert outcome["end_reason"] == "elicitation"
        assert outcome["timed_out"] is False
        assert outcome["needs_input"]["elicitation_id"] == "elicit_scripted"
        assert outcome["needs_input"]["message"] == "Need a choice"
    finally:
        await _unbind_scripted(sessions_module, original, runner)


async def test_confirmed_interrupt_and_stop_reach_the_runner(
    mcp_client: httpx.AsyncClient,
) -> None:
    """Dry-run posts nothing. confirm true delivers interrupt and stop_session."""
    session_id = await _confirmed_session(mcp_client, "control-agent")
    sessions_module, original, runner = await _bind_scripted(session_id, "record")
    try:
        for name in ("interrupt_turn", "stop_session"):
            dry = await mcp_client.post(
                "/mcp",
                json=_rpc_body(
                    "tools/call",
                    {"name": name, "arguments": {"session_id": session_id, "confirm": "true"}},
                ),
            )
            assert _tool_text(dry)["dry_run"] is True
        assert runner.posts == []

        interrupted = await mcp_client.post(
            "/mcp",
            json=_rpc_body(
                "tools/call",
                {
                    "name": "interrupt_turn",
                    "arguments": {"session_id": session_id, "confirm": True},
                },
            ),
        )
        assert _tool_text(interrupted)["queued"] is False
        stopped = await mcp_client.post(
            "/mcp",
            json=_rpc_body(
                "tools/call",
                {"name": "stop_session", "arguments": {"session_id": session_id, "confirm": True}},
            ),
        )
        assert _tool_text(stopped)["queued"] is False
        bodies = [body for _path, body in runner.posts]
        assert {"type": "interrupt"} in bodies
        assert {"type": "stop_session"} in bodies
    finally:
        await _unbind_scripted(sessions_module, original, runner)


# ── auth and the config flag ────────────────────────────────────


def _accounts_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OMNIGENT_AUTH_PROVIDER", "accounts")
    monkeypatch.setenv("OMNIGENT_ACCOUNTS_COOKIE_SECRET", "ab" * 32)
    monkeypatch.setenv("OMNIGENT_ACCOUNTS_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("OMNIGENT_ACCOUNTS_INIT_ADMIN_PASSWORD", "admin-pw-12345")
    monkeypatch.setenv("OMNIGENT_ACCOUNTS_INIT_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("OMNIGENT_ADMIN_CREDENTIALS_PATH", str(tmp_path / "admin-creds"))
    monkeypatch.setenv("OMNIGENT_ACCOUNTS_AUTO_OPEN", "0")
    monkeypatch.delenv("OMNIGENT_OIDC_ISSUER", raising=False)


def test_accounts_mode_requires_bearer_or_cookie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unauthenticated ``/mcp`` is 401; the login JWT works as Bearer and as the session cookie."""
    _accounts_env(monkeypatch, tmp_path)
    app = _sync_app(
        tmp_path,
        monkeypatch,
        auth_provider=create_auth_provider(),
        server_config={},
        accounts=True,
    )
    init = _rpc_body("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
    with TestClient(app) as anon:
        denied = anon.post("/mcp", json=init)
        assert denied.status_code == 401
        assert denied.json() == {
            "error": {"code": "unauthorized", "message": "Authentication required"}
        }
        assert anon.get("/mcp").status_code == 401
        login = anon.post("/auth/login", json={"username": "admin", "password": "admin-pw-12345"})
        assert login.status_code == 200, login.text
        token = login.json()["token"]
        assert token.count(".") == 2

    with TestClient(app) as bearer:
        accepted = bearer.post("/mcp", json=init, headers={"Authorization": f"Bearer {token}"})
        assert accepted.status_code == 200, accepted.text
        sessions = bearer.post(
            "/mcp",
            json=_rpc_body("tools/call", {"name": "list_sessions", "arguments": {}}),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert sessions.status_code == 200, sessions.text
        assert _tool_text(sessions)["data"] == []
        agents = bearer.post(
            "/mcp",
            json=_rpc_body(
                "tools/call",
                {"name": "create_session", "arguments": {"agent_name": "debby"}},
            ),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert agents.status_code == 200, agents.text
        planned = _tool_text(agents)
        assert planned["dry_run"] is True
        assert planned["path"] == "/v1/sessions"
        assert isinstance(planned["body"]["agent_id"], str)
        assert len(planned["body"]["agent_id"]) == 32

    with TestClient(app) as cookie:
        cookie.cookies.set("ap_session", token)
        cooked = cookie.post("/mcp", json=init)
        assert cooked.status_code == 200, cooked.text


def test_bearer_token_is_absent_from_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The accounts JWT is accepted and is not written to the server log."""
    caplog.set_level(logging.DEBUG)
    _accounts_env(monkeypatch, tmp_path)
    app = _sync_app(
        tmp_path,
        monkeypatch,
        auth_provider=create_auth_provider(),
        server_config={},
        accounts=True,
    )
    init = _rpc_body("initialize", {"protocolVersion": "2025-03-26"})
    with TestClient(app) as client:
        login = client.post(
            "/auth/login", json={"username": "admin", "password": "admin-pw-12345"}
        )
        assert login.status_code == 200, login.text
        token = login.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.post("/mcp", json=init, headers=headers).status_code == 200
        health = client.post(
            "/mcp", json=_rpc_body("tools/call", {"name": "health"}), headers=headers
        )
        assert _tool_text(health) == {"status": "ok"}
    assert token not in caplog.text


def test_header_mode_matches_other_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Local single-user allows ``/mcp``; header mode without an identity is 401."""
    monkeypatch.setenv("OMNIGENT_AUTH_PROVIDER", "header")
    local = UnifiedAuthProvider(
        source="header", local_single_user=True, header_name="X-Forwarded-Email"
    )
    local_app = _sync_app(tmp_path / "local", monkeypatch, auth_provider=local, server_config={})
    with TestClient(local_app) as client:
        assert client.post("/mcp", json=_rpc_body("initialize")).status_code == 200
        health = client.post("/mcp", json=_rpc_body("tools/call", {"name": "health"}))
        assert _tool_text(health) == {"status": "ok"}

    closed = UnifiedAuthProvider(
        source="header", local_single_user=False, header_name="X-Forwarded-Email"
    )
    closed_app = _sync_app(
        tmp_path / "closed", monkeypatch, auth_provider=closed, server_config={}
    )
    with TestClient(closed_app) as client:
        denied = client.post("/mcp", json=_rpc_body("initialize"))
        assert denied.status_code == 401
        sessions = client.get("/v1/sessions")
        assert sessions.status_code == 401
        allowed = client.post(
            "/mcp",
            json=_rpc_body("tools/call", {"name": "list_sessions"}),
            headers={"X-Forwarded-Email": "alice@example.com"},
        )
        assert allowed.status_code == 200, allowed.text
        assert _tool_text(allowed)["object"] == "list"


def test_disabled_flag_returns_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``mcp_enabled: false`` removes the endpoint instead of serving the SPA shell."""
    monkeypatch.delenv("OMNIGENT_AUTH_PROVIDER", raising=False)
    app = _sync_app(
        tmp_path, monkeypatch, auth_provider=None, server_config={"mcp_enabled": False}
    )
    with TestClient(app) as client:
        response = client.post("/mcp", json=_rpc_body("initialize"))
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
        plain = client.post("/mcp", content=b"{}", headers={"content-type": "text/plain"})
        assert plain.status_code == 404


def test_plain_text_body_is_unsupported(mcp_app: FastAPI) -> None:
    """A cross-site simple request cannot drive ``/mcp``. JSON is required first."""
    with TestClient(mcp_app) as client:
        response = client.post(
            "/mcp",
            content=json.dumps(
                _rpc_body(
                    "tools/call",
                    {
                        "name": "create_session",
                        "arguments": {"agent_name": "x", "confirm": True},
                    },
                )
            ),
            headers={"content-type": "text/plain", "origin": "https://evil.example"},
        )
    assert response.status_code == 415


def test_untrusted_origin_is_forbidden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Local single-user mode rejects a non-loopback Origin and still allows a missing one."""
    monkeypatch.setenv("OMNIGENT_LOCAL_SINGLE_USER", "1")
    monkeypatch.setenv("OMNIGENT_AUTH_PROVIDER", "header")
    provider = UnifiedAuthProvider(
        source="header", local_single_user=True, header_name="X-Forwarded-Email"
    )
    app = _sync_app(tmp_path, monkeypatch, auth_provider=provider, server_config={})
    init = _rpc_body("initialize")
    with TestClient(app) as client:
        evil = client.post("/mcp", json=init, headers={"Origin": "https://evil.example"})
        assert evil.status_code == 403
        loopback = client.post("/mcp", json=init, headers={"Origin": "http://127.0.0.1:6767"})
        assert loopback.status_code == 200, loopback.text
        absent = client.post("/mcp", json=init)
        assert absent.status_code == 200, absent.text
        before = client.get("/v1/sessions").json()["data"]
        write = client.post(
            "/mcp",
            json=_rpc_body(
                "tools/call",
                {
                    "name": "create_session",
                    "arguments": {"agent_name": "crossed", "confirm": True},
                },
            ),
            headers={"Origin": "https://evil.example"},
        )
        assert write.status_code == 403
        after = client.get("/v1/sessions").json()["data"]
    assert before == after


def test_forwarded_headers_do_not_replace_origin() -> None:
    """The inner ``/v1`` call keeps the caller's Origin and invents none."""

    def _request(headers: list[tuple[bytes, bytes]]) -> Request:
        return Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/mcp",
                "raw_path": b"/mcp",
                "query_string": b"",
                "headers": headers,
                "client": ("127.0.0.1", 1234),
                "server": ("test", 80),
            }
        )

    forwarded = _forwarded_headers(
        _request([(b"origin", b"http://127.0.0.1:6767"), (b"authorization", b"Bearer t")]),
        None,
    )
    assert forwarded["origin"] == "http://127.0.0.1:6767"
    assert "omnigent://internal" not in forwarded.values()
    assert "origin" not in _forwarded_headers(_request([]), None)


def test_protocol_version_is_negotiated(mcp_app: FastAPI) -> None:
    """An unknown protocol version becomes the latest. A supported one is echoed."""
    with TestClient(mcp_app) as client:
        unknown = client.post(
            "/mcp", json=_rpc_body("initialize", {"protocolVersion": "1999-01-01"})
        )
        assert unknown.status_code == 200, unknown.text
        assert unknown.json()["result"]["protocolVersion"] == "2025-03-26"
        assert unknown.headers["mcp-protocol-version"] == "2025-03-26"
        older = client.post(
            "/mcp", json=_rpc_body("initialize", {"protocolVersion": "2024-11-05"})
        )
        assert older.json()["result"]["protocolVersion"] == "2024-11-05"
        assert older.headers["mcp-protocol-version"] == "2024-11-05"
        missing = client.post("/mcp", json=_rpc_body("initialize", {}))
        assert missing.json()["result"]["protocolVersion"] == "2025-03-26"


def test_delegated_scope_token_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scope-bearing token is confined to the delegated allowlist, which excludes ``/mcp``."""
    from omnigent.server.routes.device_auth import mint_delegated_token

    _accounts_env(monkeypatch, tmp_path)
    app = _sync_app(
        tmp_path,
        monkeypatch,
        auth_provider=create_auth_provider(),
        server_config={},
        accounts=True,
    )
    token = mint_delegated_token(
        "mcp-machine",
        bytes.fromhex("ab" * 32),
        3600,
        "accounts",
        grant_id=None,
        client_id="mcp-test",
        jti="jti-mcp-scope",
    )
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        allowed = client.get("/v1/sessions", headers=headers)
        assert allowed.status_code == 200, allowed.text
        response = client.post("/mcp", json=_rpc_body("initialize"), headers=headers)
    assert response.status_code == 401


async def test_timeout_seconds_is_checked_only_when_waiting(mcp_client: httpx.AsyncClient) -> None:
    """An invalid timeout is a dry-run when wait is off, and an error when wait is on."""
    agent = await create_test_agent(mcp_client, name="timeout-guard")
    session_id = agent["_session_id"]
    dry = await mcp_client.post(
        "/mcp",
        json=_rpc_body(
            "tools/call",
            {
                "name": "send_message",
                "arguments": {
                    "session_id": session_id,
                    "text": "stay dry",
                    "wait": False,
                    "timeout_seconds": "nope",
                },
            },
        ),
    )
    planned = _tool_text(dry)
    assert planned["dry_run"] is True
    assert "timeout_seconds" not in planned
    waited = await mcp_client.post(
        "/mcp",
        json=_rpc_body(
            "tools/call",
            {
                "name": "send_message",
                "arguments": {
                    "session_id": session_id,
                    "text": "stay dry",
                    "wait": True,
                    "timeout_seconds": "nope",
                },
            },
        ),
    )
    result = waited.json()["result"]
    assert result["isError"] is True
    assert "timeout_seconds" in result["content"][0]["text"]


async def test_send_message_wait_requires_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``wait`` reads the session before subscribe. A stranger never joins the stream."""
    from omnigent.runtime import session_stream

    monkeypatch.setenv("OMNIGENT_AUTH_PROVIDER", "header")
    provider = UnifiedAuthProvider(
        source="header", local_single_user=True, header_name="X-Forwarded-Email"
    )
    app = _sync_app(tmp_path, monkeypatch, auth_provider=provider, server_config={})
    subscribed: list[str] = []
    original = session_stream.subscribe

    def _spy(conversation_id: str, **kwargs: Any) -> Any:
        subscribed.append(conversation_id)
        return original(conversation_id, **kwargs)

    monkeypatch.setattr(session_stream, "subscribe", _spy)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        agent = await create_test_agent(client, name="wait-acl")
        session_id = str(agent["_session_id"])
        denied = await client.post(
            "/mcp",
            json=_rpc_body(
                "tools/call",
                {
                    "name": "send_message",
                    "arguments": {
                        "session_id": session_id,
                        "text": "should not run",
                        "confirm": True,
                        "wait": True,
                    },
                },
            ),
            headers={"X-Forwarded-Email": "bob@example.com"},
        )
    assert denied.status_code == 200, denied.text
    result = denied.json()["result"]
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert f"GET /v1/sessions/{session_id}?include_items=false returned HTTP" in text
    assert subscribed == []


class _DroppingRequest:
    """Flips to disconnected once the watched POST has been issued."""

    def __init__(self) -> None:
        self.drop = False
        self.checks = 0

    async def is_disconnected(self) -> bool:
        self.checks += 1
        return self.drop


class _WatchApi:
    def __init__(self, request: _DroppingRequest, *, fail_read: bool = False) -> None:
        self._request = request
        self.fail_read = fail_read
        self.calls: list[tuple[str, str]] = []

    async def call(
        self, method: str, path: str, body: Any = None, *, timeout: float = 30.0
    ) -> Any:
        del body, timeout
        self.calls.append((method, path))
        if method == "GET" and self.fail_read:
            raise ToolFailure(f"Omnigent {method} {path} returned HTTP 403: forbidden")
        if method == "POST":
            self._request.drop = True
        return {"id": "sess"}


async def _do_not_post() -> None:
    raise AssertionError("post must not run")


async def test_wait_refuses_subscribe_without_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 403 on the session read happens before the live stream is joined."""
    subscribed: list[str] = []

    def _forbid(conversation_id: str, **kwargs: Any) -> Any:
        del kwargs
        subscribed.append(conversation_id)
        raise AssertionError("subscribe must not run")

    monkeypatch.setattr("omnigent.runtime.session_stream.subscribe", _forbid)
    api = _WatchApi(_DroppingRequest(), fail_read=True)
    with pytest.raises(ToolFailure, match="403"):
        await _watch_posted(api, "sess", _do_not_post, 30)
    assert api.calls == [("GET", "/v1/sessions/sess?include_items=false")]
    assert subscribed == []


async def test_wait_stops_when_the_client_disconnects() -> None:
    """A disconnect during the turn wait returns without sitting out the budget."""
    request = _DroppingRequest()

    async def post() -> dict[str, bool]:
        request.drop = True
        return {"ok": True}

    api = _WatchApi(request)
    started = time.monotonic()
    with pytest.raises(ToolFailure, match="client disconnected"):
        await _watch_posted(api, "sess-disconnect", post, 30)
    assert time.monotonic() - started < 2
    assert api.calls == [("GET", "/v1/sessions/sess-disconnect?include_items=false")]
    assert request.drop is True
