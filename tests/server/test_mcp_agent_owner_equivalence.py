"""An agent token can call a tool exactly when its owner can (FR-070, FR-101)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import omnigent.debug_logging as debug_logging
import omnigent.server.mcp_endpoint as mcp_endpoint
from omnigent.db.db_models import SqlChatThread, SqlConversation, SqlUser
from omnigent.db.utils import get_or_create_engine, now_epoch_us
from omnigent.server.auth import LEVEL_EDIT, LEVEL_OWNER
from omnigent.server.mcp_chat import CHAT_TOOL_NAMES
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from tests.server.chat_testkit import (
    ANNA,
    JENS,
    add_member,
    channel_id,
    chat_client,
    create_account,
    headers_for,
    rpc,
    tools_call,
)

_ = chat_client
_MUST_INCLUDE = {
    "session_workspace",
    "host_fs",
    "policy",
    "session_access",
    "sharing",
    "session_mcp",
    "manage_session",
    "get_elicitation",
    "answer_elicitation",
    *CHAT_TOOL_NAMES,
}


def _outcome(response) -> tuple:
    """Comparable tool outcome: ok, or the stable error code."""
    body = response.json()
    if response.status_code != 200 or "result" not in body:
        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        return ("http", response.status_code, code)
    result = body["result"]
    if result.get("isError") is not True:
        return ("ok",)
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        error = structured.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return ("error", error["code"])
        if isinstance(structured.get("code"), str):
            return ("error", structured["code"])
    text = result["content"][0]["text"]
    return ("error", text.split(":", 1)[0])


def _arguments(name: str, session_id: str, *, allow: bool) -> dict:
    """Arguments that do not wait on a runner."""
    if name == "x_debug_dump":
        return {"confirm": True, "allow": allow}
    if name in {
        "health",
        "info",
        "list_agents",
        "list_hosts",
        "list_harnesses",
        "list_projects",
        "list_sessions",
        "list_extensions",
        "list_skills",
        "chat_list_channels",
        "chat_unread",
    }:
        return {"confirm": True}
    if name == "create_session":
        return {"confirm": True, "agent_name": "missing-agent"}
    if name == "send_message":
        return {"session_id": session_id, "text": "hi", "confirm": True}
    if name in {"get_session", "get_session_items"}:
        return {"session_id": session_id, "confirm": True}
    if name == "host_fs":
        return {"action": "list", "host_id": "missing-host", "confirm": True}
    if name == "project":
        return {"action": "get" if allow else "delete", "project_id": "missing", "confirm": True}
    if name == "policy":
        return {
            "action": "registry" if allow else "delete",
            "policy_id": "missing",
            "scope": "global",
            "confirm": True,
        }
    if name == "sharing":
        return {"action": "get", "confirm": True}
    if name == "get_extension":
        return {"extension_id": "missing", "confirm": True}
    if name in {"chat_read", "chat_list_members", "chat_mark_read"}:
        return {"channel": "missing-channel", "confirm": True}
    if name == "chat_post":
        return {"channel": "missing-channel", "body": "x", "confirm": True}
    if name == "chat_open_dm":
        # An empty list is rejected for the owner and the bot alike. A real
        # DM would differ: the bot must include its owner (FR-067).
        return {"principals": [], "confirm": True}
    if name == "answer_elicitation" or name == "get_elicitation":
        return {
            "session_id": session_id,
            "elicitation_id": "e1",
            "action": "approve",
            "confirm": True,
        }
    return {"action": "list", "session_id": session_id, "confirm": True}


def _seed_session(db_uri: str, owner: str, *, listed: bool = False) -> str:
    """Create a conversation and grant ``owner`` ownership.

    ``listed`` binds an agent id so ``GET /v1/sessions`` includes the row.
    """
    agent_id = uuid.uuid4().hex if listed else None
    conversation = SqlAlchemyConversationStore(db_uri).create_conversation(agent_id=agent_id)
    store = SqlAlchemyPermissionStore(db_uri)
    store.ensure_user(owner)
    store.grant(owner, conversation.id, LEVEL_OWNER)
    return conversation.id


def _register_agent(db_uri: str, name: str) -> str:
    """Register a server agent whose bundle the app's artifact store can read."""
    from omnigent.runtime import get_artifact_store
    from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
    from tests.server.helpers import build_agent_bundle

    agent_id = uuid.uuid4().hex
    key = f"agents/{agent_id}/bundle.tar.gz"
    artifact_store = get_artifact_store()
    assert artifact_store is not None
    artifact_store.put(key, build_agent_bundle(name))
    SqlAlchemyAgentStore(db_uri).create(agent_id, name, key)
    return agent_id


def _conversation_count(db_uri: str) -> int:
    with Session(get_or_create_engine(db_uri)) as session:
        return session.scalar(select(func.count()).select_from(SqlConversation)) or 0


async def test_ac_070a_tools_list_matches_the_owner(chat_client) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    token = created.json()["token"]
    bot = await rpc(chat_client, "tools/list", token=token, protocol="2025-11-25")
    owner = await rpc(chat_client, "tools/list", user=JENS, protocol="2025-11-25")
    bot_names = {tool["name"] for tool in bot.json()["result"]["tools"]}
    owner_names = {tool["name"] for tool in owner.json()["result"]["tools"]}
    assert bot_names == owner_names
    assert bot_names >= _MUST_INCLUDE


async def test_ac_070b_create_session_preview_then_owner_audit(
    chat_client, db_uri: str, monkeypatch
) -> None:
    _register_agent(db_uri, "coder")
    created = await create_account(chat_client, JENS, "grok-jens")
    account = created.json()["account"]
    token = created.json()["token"]
    before = _conversation_count(db_uri)
    preview = await tools_call(
        chat_client,
        "create_session",
        {"agent_name": "coder", "confirm": False},
        token=token,
    )
    assert preview.json()["result"].get("isError") is not True, preview.text
    assert _conversation_count(db_uri) == before
    seen: list[dict] = []
    real = mcp_endpoint.add_audit_attrs

    def spy(**attrs: object) -> None:
        seen.append(dict(attrs))
        real(**attrs)

    monkeypatch.setattr(mcp_endpoint, "add_audit_attrs", spy)
    confirmed = await tools_call(
        chat_client,
        "create_session",
        {"agent_name": "coder", "confirm": True},
        token=token,
    )
    assert confirmed.status_code == 200, confirmed.text
    attrs = next(row for row in seen if row.get("action") == "mcp.tools_call")
    assert attrs["account_id"] == account["id"]
    assert attrs["owner_id"] == JENS
    assert attrs["confirm"] == "true"


async def test_ac_070c_revoked_owner_grant_is_revoked_for_the_bot(
    chat_client, db_uri: str
) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    token = created.json()["token"]
    session_id = _seed_session(db_uri, ANNA)
    permissions = SqlAlchemyPermissionStore(db_uri)
    permissions.ensure_user(JENS)
    permissions.grant(JENS, session_id, LEVEL_EDIT)
    arguments = {"action": "list", "session_id": session_id, "confirm": True}
    allowed = await tools_call(chat_client, "session_files", arguments, token=token)
    owner_allowed = await tools_call(chat_client, "session_files", arguments, user=JENS)
    assert _outcome(allowed) == _outcome(owner_allowed)
    assert _outcome(allowed)[0] == "ok"
    # Revoke through Anna's request so the server drops its access cache.
    # A full revoke hides the session: both callers get not_found, the same
    # code a user JWT gets when it no longer has a grant.
    revoked = await chat_client.delete(
        f"/v1/sessions/{session_id}/permissions/{JENS}",
        headers=headers_for(ANNA),
    )
    assert revoked.status_code == 204, revoked.text
    denied = await tools_call(chat_client, "session_files", arguments, token=token)
    owner_denied = await tools_call(chat_client, "session_files", arguments, user=JENS)
    assert _outcome(denied) == _outcome(owner_denied)
    assert _outcome(denied)[0] == "error"
    assert _outcome(denied)[1] in {"forbidden", "not_found"}


async def test_ac_070d_every_tool_matches_the_owner(chat_client, db_uri: str, monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_AGENT_MCP_RPM", "10000")
    created = await create_account(chat_client, JENS, "grok-jens")
    account = created.json()["account"]
    token = created.json()["token"]
    allowed_session = _seed_session(db_uri, JENS)
    denied_session = _seed_session(db_uri, ANNA)
    original = mcp_endpoint.tool_definitions
    real_call = mcp_endpoint._call_tool

    def with_dump() -> list[dict]:
        return [
            *original(),
            {
                "name": "x_debug_dump",
                "description": "test tool",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

    async def call_tool(request, auth, name, args):
        if name == "x_debug_dump":
            await mcp_endpoint._Api(request, auth).call("GET", "/v1/info")
            if args.get("allow") is True:
                return {"dumped": True}
            raise mcp_endpoint.ToolFailure("forbidden", code="forbidden", http_status=403)
        return await real_call(request, auth, name, args)

    monkeypatch.setattr(mcp_endpoint, "tool_definitions", with_dump)
    monkeypatch.setattr(mcp_endpoint, "_call_tool", call_tool)
    principals: list[tuple] = []
    app_cls = mcp_endpoint._InProcessApp

    class _Recording(app_cls):
        async def __call__(self, scope, receive, send):
            async def wrapped(message):
                if message["type"] == "http.response.start" and scope.get("mcp_actor"):
                    principals.append(
                        (
                            scope.get("mcp_effective_principal"),
                            scope.get("mcp_actor"),
                        )
                    )
                await send(message)

            scope["mcp_inprocess"] = True
            await self._app(scope, receive, wrapped)

    monkeypatch.setattr(mcp_endpoint, "_InProcessApp", _Recording)
    names = [tool["name"] for tool in with_dump()]
    for name in names:
        for allow, session_id in ((True, allowed_session), (False, denied_session)):
            arguments = _arguments(name, session_id, allow=allow)
            bot = await tools_call(chat_client, name, arguments, token=token)
            owner = await tools_call(chat_client, name, arguments, user=JENS)
            bot_outcome = _outcome(bot)
            owner_outcome = _outcome(owner)
            assert bot_outcome == owner_outcome, (name, allow, bot_outcome, owner_outcome)
            if owner_outcome[0] != "ok":
                assert bot_outcome[0] != "ok"
    assert principals
    assert all(item == (JENS, account["principal_id"]) for item in principals)


async def test_ac_070e_revoked_token_does_not_run_the_tool(
    chat_client, db_uri: str, monkeypatch
) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    account = created.json()["account"]
    token = created.json()["token"]
    session_id = _seed_session(db_uri, JENS)
    first = await tools_call(
        chat_client,
        "session_workspace",
        {"action": "list", "session_id": session_id, "confirm": True},
        token=token,
    )
    assert first.status_code == 200
    removed = await chat_client.delete(
        f"/v1/chat/agent-accounts/{account['id']}", headers=headers_for(JENS)
    )
    assert removed.status_code == 204, removed.text
    calls = {"n": 0}
    real = mcp_endpoint._call_tool

    async def spy(*args, **kwargs):
        calls["n"] += 1
        return await real(*args, **kwargs)

    monkeypatch.setattr(mcp_endpoint, "_call_tool", spy)
    again = await tools_call(
        chat_client,
        "session_workspace",
        {"action": "shell", "session_id": session_id, "command": "true", "confirm": True},
        token=token,
    )
    assert again.status_code == 401, again.text
    assert calls["n"] == 0


async def test_ac_101a_list_sessions_matches_the_owner(chat_client, db_uri: str) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    token = created.json()["token"]
    jens_a = _seed_session(db_uri, JENS, listed=True)
    jens_b = _seed_session(db_uri, JENS, listed=True)
    anna = _seed_session(db_uri, ANNA, listed=True)
    bot = await tools_call(chat_client, "list_sessions", {"confirm": True}, token=token)
    owner = await tools_call(chat_client, "list_sessions", {"confirm": True}, user=JENS)
    assert _outcome(bot) == ("ok",)
    bot_ids = {row["id"] for row in _rows(bot)}
    owner_ids = {row["id"] for row in _rows(owner)}
    assert bot_ids == owner_ids
    assert {jens_a, jens_b} <= bot_ids
    assert anna not in bot_ids


def _rows(response) -> list[dict]:
    import json

    result = response.json()["result"]
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        data = structured
    else:
        data = json.loads(result["content"][0]["text"])
    rows = data.get("data") or data.get("sessions") or data.get("items") or []
    return rows if isinstance(rows, list) else []


async def test_ac_101b_inprocess_header_does_not_open_sessions(chat_client) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    token = created.json()["token"]
    response = await chat_client.get(
        "/v1/sessions",
        headers={**headers_for(token=token), "X-MCP-Inprocess": "1"},
    )
    assert response.status_code == 401, response.text


async def test_ac_102a_deleted_owner_rejects_the_token(chat_client, db_uri: str) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    token = created.json()["token"]
    with Session(get_or_create_engine(db_uri)) as session:
        user = session.execute(select(SqlUser).where(SqlUser.id == JENS)).scalar_one()
        user.deleted_at = 1
        session.commit()
    listed = await rpc(chat_client, "tools/list", token=token)
    channels = await chat_client.get("/v1/chat/channels", headers=headers_for(token=token))
    assert listed.status_code == 401, listed.text
    assert channels.status_code == 401, channels.text


async def test_header_mode_bot_token_works_without_user_row(chat_client, db_uri: str) -> None:
    """Header mode has no users row. The bot token still authenticates."""
    created = await create_account(chat_client, JENS, "grok-jens", ensure_owner=False)
    assert created.status_code == 201, created.text
    token = created.json()["token"]
    with Session(get_or_create_engine(db_uri)) as session:
        row = session.execute(select(SqlUser).where(SqlUser.id == JENS)).scalar_one_or_none()
    assert row is None
    listed = await rpc(chat_client, "tools/list", token=token)
    channels = await chat_client.get("/v1/chat/channels", headers=headers_for(token=token))
    assert listed.status_code == 200, listed.text
    assert channels.status_code == 200, channels.text


async def test_ac_103a_chat_post_audits_bot_and_owner_without_the_body(
    chat_client, db_uri: str, monkeypatch
) -> None:
    created = await create_account(chat_client, JENS, "grok-jens")
    account = created.json()["account"]
    token = created.json()["token"]
    general = await channel_id(chat_client, JENS, "general")
    add_member(db_uri, general, account["principal_id"])
    seen: list[dict] = []
    real = debug_logging.add_audit_attrs

    def spy(**attrs: object) -> None:
        seen.append(dict(attrs))
        real(**attrs)

    monkeypatch.setattr(debug_logging, "add_audit_attrs", spy)
    marker = "MARKER-55aa"
    posted = await chat_client.post(
        f"/v1/chat/channels/{general}/messages",
        json={"body": marker},
        headers=headers_for(token=token),
    )
    assert posted.status_code == 201, posted.text
    merged = {key: value for row in seen for key, value in row.items()}
    assert merged["actor_kind"] == "agent_account"
    assert merged["account_id"] == account["id"]
    assert merged["owner_id"] == JENS
    assert merged["action"] == "chat.post"
    assert merged["message_id"] == posted.json()["id"]
    assert marker not in " ".join(str(value) for row in seen for value in row.values())


async def test_ac_103b_inprocess_session_create_audits_the_new_session(
    chat_client, db_uri: str, monkeypatch
) -> None:
    _register_agent(db_uri, "coder")
    created = await create_account(chat_client, JENS, "grok-jens")
    account = created.json()["account"]
    token = created.json()["token"]
    seen: list[dict] = []
    real = debug_logging.add_audit_attrs

    def spy(**attrs: object) -> None:
        seen.append(dict(attrs))
        real(**attrs)

    monkeypatch.setattr(debug_logging, "add_audit_attrs", spy)
    called = await tools_call(
        chat_client,
        "create_session",
        {"agent_name": "coder", "confirm": True},
        token=token,
    )
    assert called.json()["result"].get("isError") is not True, called.text
    merged = {key: value for row in seen for key, value in row.items()}
    assert merged["actor_kind"] == "agent_account"
    assert merged["account_id"] == account["id"]
    assert merged["owner_id"] == JENS
    assert merged["action"] == "sessions.create"
    assert isinstance(merged.get("session_id"), str) and merged["session_id"]


async def test_ac_104a_configured_quota_blocks_the_next_start(
    chat_client, db_uri: str, monkeypatch
) -> None:
    monkeypatch.setenv("OMNIGENT_BOT_SESSION_QUOTA", "2")
    created = await create_account(chat_client, JENS, "grok-jens")
    account = created.json()["account"]
    token = created.json()["token"]
    general = await channel_id(chat_client, JENS, "general")
    add_member(db_uri, general, account["principal_id"])
    now = now_epoch_us()
    with Session(get_or_create_engine(db_uri)) as session:
        for _ in range(2):
            session.add(
                SqlChatThread(
                    root_message_id=uuid.uuid4().hex,
                    channel_id=general,
                    reply_count=0,
                    participants="[]",
                    bot_streak=0,
                    agent_state="active",
                    session_id=uuid.uuid4().hex,
                    caller_id=JENS,
                    started_by_agent_account_id=account["id"],
                    start_requested_at=now,
                    updated_at=now,
                )
            )
        session.commit()
    from omnigent.server.chat_agent_threads import drain_pending

    posted = await chat_client.post(
        f"/v1/chat/channels/{general}/messages",
        json={"body": "<@omnigent_agent:coder> weiter"},
        headers=headers_for(token=token),
    )
    assert posted.status_code == 201, posted.text
    await drain_pending()
    messages = await chat_client.get(
        f"/v1/chat/channels/{general}/messages",
        params={"thread_root_id": posted.json()["id"], "limit": 20},
        headers=headers_for(JENS),
    )
    assert messages.status_code == 200, messages.text
    assert "Bot-Kontingent erschöpft" in [item["body"] for item in messages.json()["items"]]


async def test_quota_refusal_audits_the_owner(chat_client, db_uri: str, monkeypatch) -> None:
    """A quota refusal stays on the persisted audit row as refused/quota."""
    import logging
    import threading

    from omnigent.debug_logging import DebugLogHandler, attach_debug_log_sink
    from omnigent.server.chat_agent_threads import drain_pending

    monkeypatch.setenv("OMNIGENT_BOT_SESSION_QUOTA", "1")
    monkeypatch.setattr(DebugLogHandler, "_FLUSH_WAIT", 0.01)
    monkeypatch.setattr(debug_logging, "_active_sink", None)
    rows: list[dict] = []
    got_end = threading.Event()

    def send(batch: list) -> None:
        rows.extend(batch)
        for row in batch:
            attrs = row.get("attributes") or {}
            if attrs.get("phase") in ("ok", "error") and attrs.get("outcome") == "refused/quota":
                got_end.set()

    attach_debug_log_sink([], source="server", level=logging.INFO, send=send)
    sink = debug_logging._active_sink
    assert sink is not None
    try:
        created = await create_account(chat_client, JENS, "grok-jens")
        account = created.json()["account"]
        token = created.json()["token"]
        general = await channel_id(chat_client, JENS, "general")
        add_member(db_uri, general, account["principal_id"])
        now = now_epoch_us()
        with Session(get_or_create_engine(db_uri)) as session:
            session.add(
                SqlChatThread(
                    root_message_id=uuid.uuid4().hex,
                    channel_id=general,
                    reply_count=0,
                    participants="[]",
                    bot_streak=0,
                    agent_state="active",
                    session_id=uuid.uuid4().hex,
                    caller_id=JENS,
                    started_by_agent_account_id=account["id"],
                    start_requested_at=now,
                    updated_at=now,
                )
            )
            session.commit()
        posted = await chat_client.post(
            f"/v1/chat/channels/{general}/messages",
            json={"body": "<@omnigent_agent:coder> weiter MARKER-quota"},
            headers=headers_for(token=token),
        )
        assert posted.status_code == 201, posted.text
        await drain_pending()
        assert got_end.wait(timeout=2.0), rows
        persisted = [
            row
            for row in rows
            if (row.get("attributes") or {}).get("outcome") == "refused/quota"
            and (row.get("attributes") or {}).get("phase") in ("ok", "error")
        ]
        assert len(persisted) == 1
        attrs = persisted[0]["attributes"]
        assert attrs["action"] == "agent_thread_start"
        assert attrs["account_id"] == account["id"]
        assert attrs["owner_id"] == JENS
        assert "MARKER-quota" not in str(attrs)
    finally:
        debug_logging.audit_event_logger().removeHandler(sink)
        debug_logging.sse_event_logger().removeHandler(sink)
        sink.close()
        monkeypatch.setattr(debug_logging, "_active_sink", None)
    called = await tools_call(
        chat_client,
        "create_session",
        {"agent_name": "coder", "confirm": True},
        token=token,
    )
    text = called.json()["result"]["content"][0]["text"]
    assert called.json()["result"]["isError"] is True
    assert text.startswith("bot_session_quota: ")


async def test_ac_104b_unset_quota_allows_three_starts(
    chat_client, db_uri: str, monkeypatch
) -> None:
    """With no quota configured, three bot session creates all succeed."""
    monkeypatch.delenv("OMNIGENT_BOT_SESSION_QUOTA", raising=False)
    _register_agent(db_uri, "coder")
    created = await create_account(chat_client, JENS, "grok-jens")
    token = created.json()["token"]
    before = _conversation_count(db_uri)
    for _ in range(3):
        called = await tools_call(
            chat_client,
            "create_session",
            {"agent_name": "coder", "confirm": True},
            token=token,
        )
        result = called.json()["result"]
        text = result["content"][0]["text"]
        assert result.get("isError") is not True, text
        assert not text.startswith("bot_session_quota:")
    assert _conversation_count(db_uri) == before + 3
