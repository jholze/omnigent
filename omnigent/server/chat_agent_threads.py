"""Start one Omnigent session for a human ``<@omnigent_agent:agent>`` mention.

The thread row links that session. A later idle status copies the final
assistant text back into the thread. Message bodies are never logged.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from omnigent.db.db_models import (
    SqlChatAgentAccount,
    SqlChatMessage,
    SqlChatThread,
    SqlChatUserPref,
    current_workspace_id,
)
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch_us,
    run_write_transaction,
)
from omnigent.debug_logging import add_audit_attrs, debug_event
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import (
    LEVEL_OWNER,
    LEVEL_READ,
    SharingMode,
    workspace_sharing_blocked,
)
from omnigent.server.chat_access import principal_kind
from omnigent.server.chat_mentions import parse_mentions
from omnigent.server.chat_realtime import publish_chat, publish_message_created
from omnigent.stores.chat_store import ChatChannel, ChatMessage, ChatStore

_logger = logging.getLogger("omnigent.chat")

_HANDLE = "agent"
_LABEL_KEY = "omnigent.chat.v1"
_CONTEXT_MESSAGES = 20
_CONTEXT_CHARS = 12_000
_MIRROR_CHARS = 32_000
_MIRROR_SUFFIX = "… vollständig in der Session"
_START_TIMEOUT_US = 120_000_000
_SWEEP_INTERVAL_S = 30
_AUTHOR_AGENT = "agent"
_AUTHOR_SYSTEM = "system"
_QUERY_PREFIX = "omnigent.chat_agent_threads"
_REASON_CLASSES = frozenset(
    {"no_project", "host_offline", "agent_not_found", "forbidden", "timeout", "internal"}
)
_FAILURE_TEXT = {
    "no_project": (
        "Kein Projekt für Agenten-Start – Standard in Einstellungen setzen (no_project)"
    ),
    "host_offline": "Host offline (host_offline)",
    "agent_not_found": "Agent nicht gefunden (agent_not_found)",
    "forbidden": "Zugriff verweigert (forbidden)",
    "timeout": "Agent-Start hat das Zeitlimit überschritten (timeout)",
    "internal": "Agent-Start fehlgeschlagen (internal)",
}

_makers: dict[str, Any] = {}
_bound_chat_store: ChatStore | None = None
_pending: set[asyncio.Task[None]] = set()
_timeout_task: asyncio.Task[None] | None = None
_result_counts: dict[str, int] = {}
# Mirrors scheduled off the event loop. The status publish returns first.
_status_jobs: set[asyncio.Future[Any]] = set()
# One mirror at a time, so a repeated idle observes the row it just inserted.
_status_gate = threading.RLock()


class _StartFailure(Exception):
    """A classified start failure whose text is safe to show in the thread."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason if reason in _REASON_CLASSES else "internal"


@dataclass(frozen=True)
class _Notice:
    """A committed thread change to publish after the write transaction."""

    channel_id: str
    root_id: str
    reply_count: int
    agent_state: str
    session_id: str | None
    bot_streak: int
    message: ChatMessage | None = None


def _maker(chat_store: ChatStore) -> Any:
    """Return the named session maker for this chat database."""
    uri = chat_store.storage_location
    maker = _makers.get(uri)
    if maker is None:
        maker = make_named_managed_session_maker(
            get_or_create_engine(uri),
            query_name_prefix=_QUERY_PREFIX,
        )
        _makers[uri] = maker
    return maker


def _read(chat_store: ChatStore, query_name: str, callback: Callable[[Session], Any]) -> Any:
    """Run one named read against the chat database."""
    with _maker(chat_store)(query_name) as session:
        return callback(session)


def _write(chat_store: ChatStore, query_name: str, callback: Callable[[Session], Any]) -> Any:
    """Run one named write, replaying transient serialization failures."""
    return run_write_transaction(_maker(chat_store), query_name, callback)


def _bind_store(chat_store: ChatStore) -> None:
    """Remember the chat database the status hook mirrors into."""
    global _bound_chat_store
    _bound_chat_store = chat_store


def _mentions_agent(body: str) -> bool:
    """Whether the body addresses the project agent handle."""
    return any(
        token.kind == "omnigent_agent" and token.target_id == _HANDLE
        for token in parse_mentions(body)
    )


def _display_in(session: Session, principal_id: str) -> str:
    """Display name for a context prefix or the sharing notice."""
    if principal_id.startswith("agent:"):
        name = session.execute(
            select(SqlChatAgentAccount.display_name).where(
                SqlChatAgentAccount.workspace_id == current_workspace_id(),
                SqlChatAgentAccount.principal_id == principal_id,
                SqlChatAgentAccount.revoked_at.is_(None),
            )
        ).scalar_one_or_none()
        if isinstance(name, str) and name.strip():
            return name.strip()
    local = principal_id.split("@", 1)[0].strip() or principal_id
    return local[:1].upper() + local[1:]


def _record(
    reason: str,
    *,
    channel_id: str,
    message_id: str,
    caller: str,
    session_id: str | None,
) -> None:
    """Count one start outcome. The row names the class, never the message."""
    _result_counts[reason] = _result_counts.get(reason, 0) + 1
    add_audit_attrs(
        action="agent_thread_start",
        channel_id=channel_id,
        message_id=message_id,
        account_id=caller,
        result=reason,
    )
    _logger.info(
        "agent thread start result=%s count=%s",
        reason,
        _result_counts[reason],
        extra=debug_event(
            "agent_thread_start",
            session_id=session_id,
            user_id=caller,
            result=reason,
            channel_id=channel_id,
            message_id=message_id,
            count=_result_counts[reason],
        ),
    )


def _claim(chat_store: ChatStore, channel_id: str, root_id: str, caller: str) -> bool:
    """Move a thread with no agent from ``none`` to ``starting``, once."""

    def write(session: Session) -> bool:
        now = now_epoch_us()
        claimed = cast(
            CursorResult[Any],
            session.execute(
                update(SqlChatThread)
                .where(
                    SqlChatThread.workspace_id == current_workspace_id(),
                    SqlChatThread.root_message_id == root_id,
                    SqlChatThread.agent_state == "none",
                )
                .values(
                    agent_state="starting",
                    agent_handle=_HANDLE,
                    caller_id=caller,
                    start_requested_at=now,
                    updated_at=now,
                )
            ),
        )
        if claimed.rowcount == 1:
            return True
        existing = session.execute(
            select(SqlChatThread.root_message_id).where(
                SqlChatThread.workspace_id == current_workspace_id(),
                SqlChatThread.root_message_id == root_id,
            )
        ).first()
        if existing is not None:
            return False
        session.add(
            SqlChatThread(
                workspace_id=current_workspace_id(),
                root_message_id=root_id,
                channel_id=channel_id,
                reply_count=0,
                participants="[]",
                agent_state="starting",
                agent_handle=_HANDLE,
                caller_id=caller,
                start_requested_at=now,
                updated_at=now,
            )
        )
        return True

    try:
        return bool(_write(chat_store, "claim_agent_start", write))
    except IntegrityError:
        return False


def _thread_notice(session: Session, channel_id: str, root_id: str) -> _Notice | None:
    """Snapshot the thread row for a body-free frame."""
    thread = session.execute(
        select(SqlChatThread).where(
            SqlChatThread.workspace_id == current_workspace_id(),
            SqlChatThread.root_message_id == root_id,
        )
    ).scalar_one_or_none()
    if thread is None:
        return None
    return _Notice(
        channel_id=channel_id,
        root_id=root_id,
        reply_count=thread.reply_count,
        agent_state=thread.agent_state,
        session_id=thread.session_id,
        bot_streak=thread.bot_streak,
    )


def _publish_thread(chat_store: ChatStore, channel_id: str, root_id: str) -> None:
    """Publish the current thread state without a message body."""
    notice = _read(
        chat_store,
        "select_agent_thread",
        lambda session: _thread_notice(session, channel_id, root_id),
    )
    if notice is not None:
        _emit(chat_store, notice)


def _insert_reply(
    session: Session,
    *,
    channel_id: str,
    root_id: str,
    author_id: str,
    author_kind: str,
    body: str,
    system_code: str | None = None,
    session_ref: str | None = None,
    response_ref: str | None = None,
) -> _Notice:
    """Insert one thread reply and bump the thread counters."""
    now = now_epoch_us()
    message_id = uuid.uuid4().hex
    session.add(
        SqlChatMessage(
            workspace_id=current_workspace_id(),
            id=message_id,
            channel_id=channel_id,
            thread_root_id=root_id,
            author_id=author_id,
            author_kind=author_kind,
            created_at=now,
            body=body,
            system_code=system_code,
            session_ref=session_ref,
            response_ref=response_ref,
        )
    )
    thread = session.execute(
        select(SqlChatThread)
        .where(
            SqlChatThread.workspace_id == current_workspace_id(),
            SqlChatThread.root_message_id == root_id,
        )
        .with_for_update()
    ).scalar_one()
    participants = json.loads(thread.participants or "[]")
    if not isinstance(participants, list):
        participants = []
    participants = [author_id] + [item for item in participants if item != author_id]
    thread.participants = json.dumps(participants[:5])
    thread.reply_count = thread.reply_count + 1
    thread.last_reply_at = now
    thread.updated_at = now
    return _Notice(
        channel_id=channel_id,
        root_id=root_id,
        reply_count=thread.reply_count,
        agent_state=thread.agent_state,
        session_id=thread.session_id,
        bot_streak=thread.bot_streak,
        message=ChatMessage(
            id=message_id,
            channel_id=channel_id,
            thread_root_id=root_id,
            author_id=author_id,
            author_kind=author_kind,
            created_at=now,
            body=body,
            client_id=None,
        ),
    )


def _emit(chat_store: ChatStore, notice: _Notice) -> None:
    """Fan out a stored reply and the matching thread frame. Neither carries a body."""
    channel = chat_store.get_channel(notice.channel_id)
    if channel is None:
        return
    if notice.message is not None:
        publish_message_created(chat_store, channel, notice.message)
    members = [member.principal_id for member in chat_store.list_members(channel.id)]
    publish_chat(
        members,
        {
            "kind": "thread.updated",
            "channel_id": notice.channel_id,
            "root_message_id": notice.root_id,
            "reply_count": notice.reply_count,
            "agent_state": notice.agent_state,
            "session_id": notice.session_id,
            "bot_streak": notice.bot_streak,
        },
    )


def _fail(
    chat_store: ChatStore,
    *,
    channel_id: str,
    root_id: str,
    message_id: str,
    caller: str,
    reason: str,
) -> None:
    """Mark a still-starting thread failed and post one classed system line."""
    reason = reason if reason in _REASON_CLASSES else "internal"
    body = _FAILURE_TEXT[reason]

    def write(session: Session) -> _Notice | None:
        now = now_epoch_us()
        changed = cast(
            CursorResult[Any],
            session.execute(
                update(SqlChatThread)
                .where(
                    SqlChatThread.workspace_id == current_workspace_id(),
                    SqlChatThread.root_message_id == root_id,
                    SqlChatThread.agent_state == "starting",
                )
                .values(agent_state="start_failed", updated_at=now)
            ),
        )
        if changed.rowcount != 1:
            return None
        return _insert_reply(
            session,
            channel_id=channel_id,
            root_id=root_id,
            author_id=_AUTHOR_SYSTEM,
            author_kind="system",
            body=body,
            system_code=reason,
        )

    notice = _write(chat_store, "mark_agent_start_failed", write)
    if notice is None:
        return
    _emit(chat_store, notice)
    _record(reason, channel_id=channel_id, message_id=message_id, caller=caller, session_id=None)


def _mark_active(
    chat_store: ChatStore,
    *,
    channel_id: str,
    root_id: str,
    session_id: str,
    agent_id: str | None,
) -> _Notice | None:
    """Link the session once the first message has been accepted."""

    def write(session: Session) -> _Notice | None:
        now = now_epoch_us()
        changed = cast(
            CursorResult[Any],
            session.execute(
                update(SqlChatThread)
                .where(
                    SqlChatThread.workspace_id == current_workspace_id(),
                    SqlChatThread.root_message_id == root_id,
                    SqlChatThread.agent_state == "starting",
                )
                .values(
                    agent_state="active",
                    session_id=session_id,
                    agent_id=agent_id,
                    agent_handle=_HANDLE,
                    updated_at=now,
                )
            ),
        )
        if changed.rowcount != 1:
            return None
        return _thread_notice(session, channel_id, root_id)

    return _write(chat_store, "mark_agent_thread_active", write)


def _default_project_id(chat_store: ChatStore, caller: str) -> str | None:
    """Return the caller's chat default project, if one is stored."""

    def read(session: Session) -> str | None:
        return session.execute(
            select(SqlChatUserPref.default_project_id).where(
                SqlChatUserPref.workspace_id == current_workspace_id(),
                SqlChatUserPref.principal_id == caller,
            )
        ).scalar_one_or_none()

    return _read(chat_store, "select_chat_default_project", read)


def _resolve_project(
    project_store: Any, chat_store: ChatStore, caller: str, channel: ChatChannel
) -> Any:
    """Channel project name wins; otherwise the caller's chat default."""
    name = (channel.project_name or "").strip()
    if name:
        for project in project_store.list(user_id=caller):
            if project.name == name:
                return project
        return None
    project_id = _default_project_id(chat_store, caller)
    if project_id is None:
        return None
    return project_store.get(project_id, user_id=caller)


def _live_host_id(host_store: Any, caller: str, project: Any) -> str | None:
    """Return a live host id from the project, or None when the project sets none."""
    config = project.config if isinstance(project.config, dict) else {}
    host_id = config.get("host_id")
    if not isinstance(host_id, str) or not host_id:
        return None
    if host_store is None:
        raise _StartFailure("host_offline")
    host = host_store.get_host(host_id)
    if host is None or not _host_is_live(host):
        raise _StartFailure("host_offline")
    if host.user_id != caller:
        raise _StartFailure("forbidden")
    return host_id


def _host_is_live(host: Any) -> bool:
    from omnigent.stores.host_store import host_is_live

    return bool(host_is_live(host))


def _context_text(
    chat_store: ChatStore, channel_id: str, root_id: str, trigger: ChatMessage
) -> str:
    """Trigger plus the newest preceding thread lines, capped at 12 000 characters."""

    def read(session: Session) -> str:
        rows = (
            session.execute(
                select(SqlChatMessage)
                .where(
                    SqlChatMessage.workspace_id == current_workspace_id(),
                    SqlChatMessage.channel_id == channel_id,
                    SqlChatMessage.deleted_at.is_(None),
                    SqlChatMessage.author_kind != "system",
                    (SqlChatMessage.id == root_id) | (SqlChatMessage.thread_root_id == root_id),
                )
                .order_by(SqlChatMessage.created_at, SqlChatMessage.id)
            )
            .scalars()
            .all()
        )
        lines_src = [(row.created_at, row.id, row.author_id, row.body) for row in rows]
        if not any(item_id == trigger.id for _, item_id, _, _ in lines_src):
            lines_src.append((trigger.created_at, trigger.id, trigger.author_id, trigger.body))
        lines_src.sort()
        trigger_key = (trigger.created_at, trigger.id)
        preceding = [item for item in lines_src if (item[0], item[1]) < trigger_key]
        chosen = preceding[-_CONTEXT_MESSAGES:]
        chosen.append(next(item for item in lines_src if (item[0], item[1]) == trigger_key))
        rendered = [f"[{_display_in(session, author)}] {body}" for _, _, author, body in chosen]
        while len(rendered) > 1 and len("\n".join(rendered)) > _CONTEXT_CHARS:
            rendered.pop(0)
        text = "\n".join(rendered)
        if len(text) > _CONTEXT_CHARS:
            text = text[-_CONTEXT_CHARS:]
        return text

    return str(_read(chat_store, "select_agent_thread_context", read))


def _find_post_event(app: Any) -> Any:
    """Return the existing session-event endpoint, without an HTTP self-call."""
    pending = list(getattr(app, "routes", []))
    while pending:
        route = pending.pop()
        nested = getattr(route, "routes", None)
        if nested:
            pending.extend(list(nested))
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if (
            isinstance(path, str)
            and path.endswith("/sessions/{session_id}/events")
            and "POST" in methods
        ):
            return getattr(route, "endpoint", None)
    return None


def _classify(exc: OmnigentError) -> str:
    """Map a create or first-message error onto one public reason class."""
    message = exc.message.lower()
    if exc.code == ErrorCode.NOT_FOUND and "project" in message:
        return "no_project"
    if "agent_id is required" in message or (
        exc.code == ErrorCode.NOT_FOUND and "agent" in message
    ):
        return "agent_not_found"
    if exc.code == ErrorCode.RUNNER_UNAVAILABLE or "runner" in message:
        return "host_offline"
    if exc.http_status in (403, 404):
        return "forbidden"
    return "internal"


def _sharing_mode(request: Any) -> SharingMode:
    resolver = getattr(request.app.state, "sharing_mode", None)
    if callable(resolver):
        return SharingMode.coerce(resolver())
    return SharingMode.ON


def _grant_owner(permission_store: Any, caller: str, session_id: str) -> None:
    permission_store.ensure_user(caller)
    permission_store.grant(caller, session_id, LEVEL_OWNER)


def _grant_readers(
    permission_store: Any,
    chat_store: ChatStore,
    channel_id: str,
    caller: str,
    session_id: str,
) -> None:
    """Grant READ to every other human member. One failure does not abort the rest."""
    for member in chat_store.list_members(channel_id):
        principal_id = member.principal_id
        if principal_id == caller or principal_kind(principal_id) != "user":
            continue
        try:
            permission_store.ensure_user(principal_id)
            permission_store.grant(principal_id, session_id, LEVEL_READ)
        except Exception:
            _logger.exception(
                "agent thread read grant failed session=%s member=%s",
                session_id,
                principal_id,
            )


def _post_sharing_notice(
    chat_store: ChatStore, channel_id: str, root_id: str, caller: str
) -> None:
    name = _read(
        chat_store,
        "select_display_name",
        lambda session: _display_in(session, caller),
    )
    body = f"Live-Ansicht nur für {name} (Freigabe am Server deaktiviert)"

    def write(session: Session) -> _Notice:
        return _insert_reply(
            session,
            channel_id=channel_id,
            root_id=root_id,
            author_id=_AUTHOR_SYSTEM,
            author_kind="system",
            body=body,
            system_code="sharing_disabled",
        )

    _emit(chat_store, _write(chat_store, "insert_sharing_notice", write))


def _stores(request: Any, chat_store: ChatStore) -> tuple[Any, Any, Any, Any, Any]:
    """Resolve the session stores that share the chat database."""
    from omnigent.runtime import get_agent_store, get_conversation_store
    from omnigent.stores.host_store import HostStore
    from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
    from omnigent.stores.project_store.sqlalchemy_store import SqlAlchemyProjectStore

    try:
        conversation_store = get_conversation_store()
    except RuntimeError:
        from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore

        conversation_store = SqlAlchemyConversationStore(chat_store.storage_location)
    agent_store = getattr(request.app.state, "agent_store", None)
    if agent_store is None:
        agent_store = get_agent_store()
    host_store = getattr(request.app.state, "host_store", None)
    if host_store is None:
        host_store = HostStore(chat_store.storage_location)
    project_store = SqlAlchemyProjectStore(chat_store.storage_location)
    permission_store = SqlAlchemyPermissionStore(chat_store.storage_location)
    return conversation_store, agent_store, host_store, project_store, permission_store


async def _post_first_message(request: Any, session_id: str, text: str) -> None:
    """Send the thread context through the existing session-event handler."""
    from omnigent.server.schemas import SessionEventInput

    endpoint = _find_post_event(request.app)
    if endpoint is None:
        raise _StartFailure("internal")
    result = await endpoint(
        request,
        session_id,
        SessionEventInput(
            type="message",
            data={"role": "user", "content": [{"type": "input_text", "text": text}]},
        ),
    )
    if isinstance(result, dict) and result.get("denied"):
        raise _StartFailure("forbidden")
    if isinstance(result, dict) and result.get("queued") is False:
        raise _StartFailure("internal")


async def _start_impl(
    request: Any,
    chat_store: ChatStore,
    channel: ChatChannel,
    message: ChatMessage,
    root_id: str,
) -> None:
    """Create the caller's session, grant access, and send the thread context."""
    from omnigent.server.routes._sessions.orchestration import _create_session_from_existing_agent
    from omnigent.server.schemas import ProjectSessionCreateRequest

    caller = message.author_id
    (
        conversation_store,
        agent_store,
        host_store,
        project_store,
        permission_store,
    ) = _stores(request, chat_store)
    project = _resolve_project(project_store, chat_store, caller, channel)
    if project is None:
        raise _StartFailure("no_project")
    host_id = _live_host_id(host_store, caller, project)
    label = json.dumps(
        {"channel_id": channel.id, "thread_root_id": root_id},
        separators=(",", ":"),
    )
    create_kwargs: dict[str, Any] = {
        "project_id": project.id,
        "labels": {_LABEL_KEY: label},
    }
    if host_id is None:
        # A project git hint cannot be applied until a host is bound.
        create_kwargs["git"] = None
    else:
        create_kwargs["host_id"] = host_id
    body = ProjectSessionCreateRequest(**create_kwargs)
    response, conversation = await _create_session_from_existing_agent(
        conversation_store,
        agent_store,
        getattr(request.app.state, "runner_router", None),
        body,
        request,
        user_id=caller,
        permission_store=permission_store,
        project_store=project_store,
        background_title_coordinator=getattr(
            request.app.state, "background_title_coordinator", None
        ),
    )
    session_id = response.id
    await asyncio.to_thread(_grant_owner, permission_store, caller, session_id)
    workspace = conversation.workspace if conversation is not None else None
    sharing = _sharing_mode(request)
    if sharing == SharingMode.OFF or (
        sharing == SharingMode.RESTRICTED_READ_ONLY and workspace_sharing_blocked(workspace)
    ):
        await asyncio.to_thread(_post_sharing_notice, chat_store, channel.id, root_id, caller)
    else:
        await asyncio.to_thread(
            _grant_readers,
            permission_store,
            chat_store,
            channel.id,
            caller,
            session_id,
        )
    text = await asyncio.to_thread(_context_text, chat_store, channel.id, root_id, message)
    await _post_first_message(request, session_id, text)
    notice = await asyncio.to_thread(
        _mark_active,
        chat_store,
        channel_id=channel.id,
        root_id=root_id,
        session_id=session_id,
        agent_id=response.agent_id,
    )
    if notice is None:
        return
    _emit(chat_store, notice)
    _record(
        "active",
        channel_id=channel.id,
        message_id=message.id,
        caller=caller,
        session_id=session_id,
    )


async def _run_start(
    request: Any,
    chat_store: ChatStore,
    channel: ChatChannel,
    message: ChatMessage,
    root_id: str,
) -> None:
    """Run one claimed start and collapse every failure into a system line."""
    try:
        await _start_impl(request, chat_store, channel, message, root_id)
    except _StartFailure as exc:
        await asyncio.to_thread(
            _fail,
            chat_store,
            channel_id=channel.id,
            root_id=root_id,
            message_id=message.id,
            caller=message.author_id,
            reason=exc.reason,
        )
    except OmnigentError as exc:
        reason = _classify(exc)
        if reason == "internal":
            _logger.exception(
                "agent thread start failed channel=%s root=%s class=%s",
                channel.id,
                root_id,
                reason,
            )
        await asyncio.to_thread(
            _fail,
            chat_store,
            channel_id=channel.id,
            root_id=root_id,
            message_id=message.id,
            caller=message.author_id,
            reason=reason,
        )
    except Exception:
        _logger.exception(
            "agent thread start failed channel=%s root=%s class=internal",
            channel.id,
            root_id,
        )
        await asyncio.to_thread(
            _fail,
            chat_store,
            channel_id=channel.id,
            root_id=root_id,
            message_id=message.id,
            caller=message.author_id,
            reason="internal",
        )


async def schedule_after_human_post(
    request: Any,
    chat_store: ChatStore,
    channel: ChatChannel,
    message: ChatMessage,
) -> None:
    """Claim and start an agent thread after a newly stored human message.

    A replayed post, a non-human author, any handle other than ``agent``,
    and a thread that is already starting or active do not create a session.
    """
    try:
        _bind_store(chat_store)
        _ensure_timeout_loop(chat_store)
        if principal_kind(message.author_id) != "user" or not _mentions_agent(message.body):
            return
        root_id = message.thread_root_id or message.id
        claimed = await asyncio.to_thread(
            _claim, chat_store, channel.id, root_id, message.author_id
        )
        if not claimed:
            return
        await asyncio.to_thread(_publish_thread, chat_store, channel.id, root_id)
        task = asyncio.create_task(_run_start(request, chat_store, channel, message, root_id))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except Exception:
        _logger.exception(
            "agent thread schedule failed channel=%s message=%s",
            channel.id,
            message.id,
        )


async def drain_pending() -> None:
    """Wait until every start scheduled in this process has finished."""
    pending = [task for task in list(_pending) if not task.done()]
    if pending:
        await asyncio.gather(*pending)


def _fail_stale_starts(chat_store: ChatStore) -> None:
    """Move every start older than 120 seconds to ``start_failed`` / ``timeout``."""
    cutoff = now_epoch_us() - _START_TIMEOUT_US

    def write(session: Session) -> list[_Notice]:
        rows = session.execute(
            select(
                SqlChatThread.root_message_id,
                SqlChatThread.channel_id,
                SqlChatThread.caller_id,
            ).where(
                SqlChatThread.workspace_id == current_workspace_id(),
                SqlChatThread.agent_state == "starting",
                SqlChatThread.start_requested_at.is_not(None),
                SqlChatThread.start_requested_at < cutoff,
            )
        ).all()
        notices: list[_Notice] = []
        for root_id, channel_id, caller_id in rows:
            now = now_epoch_us()
            changed = cast(
                CursorResult[Any],
                session.execute(
                    update(SqlChatThread)
                    .where(
                        SqlChatThread.workspace_id == current_workspace_id(),
                        SqlChatThread.root_message_id == root_id,
                        SqlChatThread.agent_state == "starting",
                        SqlChatThread.start_requested_at < cutoff,
                    )
                    .values(agent_state="start_failed", updated_at=now)
                ),
            )
            if changed.rowcount != 1:
                continue
            notices.append(
                _insert_reply(
                    session,
                    channel_id=channel_id,
                    root_id=root_id,
                    author_id=_AUTHOR_SYSTEM,
                    author_kind="system",
                    body=_FAILURE_TEXT["timeout"],
                    system_code="timeout",
                )
            )
            _record(
                "timeout",
                channel_id=channel_id,
                message_id=root_id,
                caller=caller_id or _AUTHOR_SYSTEM,
                session_id=None,
            )
        return notices

    notices = _write(chat_store, "fail_stale_agent_starts", write)
    for notice in notices:
        _emit(chat_store, notice)


def reconcile_due_starts(chat_store: ChatStore) -> None:
    """Run the periodic start-timeout sweep once."""
    _bind_store(chat_store)
    _fail_stale_starts(chat_store)


async def _timeout_loop(chat_store: ChatStore) -> None:
    """Sweep stuck starts every 30 seconds until the process stops."""
    while True:
        await asyncio.sleep(_SWEEP_INTERVAL_S)
        try:
            await asyncio.to_thread(_fail_stale_starts, chat_store)
        except Exception:
            _logger.exception("agent thread start timeout sweep failed")


def _ensure_timeout_loop(chat_store: ChatStore) -> None:
    """Start the in-process sweep on the first chat request after boot."""
    global _timeout_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = _timeout_task
    if task is not None and not task.done() and task.get_loop() is loop:
        return
    _timeout_task = loop.create_task(_timeout_loop(chat_store))


async def reconcile_thread_read(chat_store: ChatStore, thread_root_id: str) -> None:
    """Apply the start timeout when a thread is read."""
    _bind_store(chat_store)
    _ensure_timeout_loop(chat_store)
    _logger.debug("agent thread read reconcile root=%s", thread_root_id)
    await asyncio.to_thread(_fail_stale_starts, chat_store)


def _assistant_text(session_id: str) -> str | None:
    """Return the newest assistant text the manager-webhook summary would send."""
    from omnigent.runtime import get_conversation_store
    from omnigent.server.manager_webhook_context import build_lookups
    from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore

    store = _bound_chat_store
    try:
        conversation_store = get_conversation_store()
    except RuntimeError:
        if store is None:
            return None
        conversation_store = SqlAlchemyConversationStore(store.storage_location)
    summary_lookup = build_lookups(conversation_store=conversation_store)[1]
    text = summary_lookup(session_id)
    return text if isinstance(text, str) and text else None


def _truncate_mirror(text: str) -> str:
    """Keep the first 32 000 characters and point the reader at the session."""
    if len(text) <= _MIRROR_CHARS:
        return text
    return text[:_MIRROR_CHARS] + _MIRROR_SUFFIX


def _mirror(
    chat_store: ChatStore,
    session_id: str,
    response_id: str,
    *,
    body: str,
    author_kind: str,
    author_id: str,
    system_code: str | None,
) -> None:
    """Store one thread reply for a session status, once per response id."""
    response_ref = response_id[:128]

    def write(session: Session) -> _Notice | None:
        thread = session.execute(
            select(SqlChatThread).where(
                SqlChatThread.workspace_id == current_workspace_id(),
                SqlChatThread.session_id == session_id,
                SqlChatThread.agent_state == "active",
            )
        ).scalar_one_or_none()
        if thread is None:
            return None
        existing = session.execute(
            select(SqlChatMessage.id).where(
                SqlChatMessage.workspace_id == current_workspace_id(),
                SqlChatMessage.session_ref == session_id,
                SqlChatMessage.response_ref == response_ref,
            )
        ).first()
        if existing is not None:
            return None
        return _insert_reply(
            session,
            channel_id=thread.channel_id,
            root_id=thread.root_message_id,
            author_id=author_id,
            author_kind=author_kind,
            body=body,
            system_code=system_code,
            session_ref=session_id,
            response_ref=response_ref,
        )

    notice = _write(chat_store, "insert_agent_thread_mirror", write)
    if notice is not None:
        _emit(chat_store, notice)


def _session_has_thread(chat_store: ChatStore, session_id: str) -> bool:
    """Whether ``chat_threads.session_id`` links this session.

    That column is indexed. Callers use the hit to skip the assistant
    summary for every other session.
    """

    def read(session: Session) -> bool:
        row = session.execute(
            select(SqlChatThread.root_message_id).where(
                SqlChatThread.workspace_id == current_workspace_id(),
                SqlChatThread.session_id == session_id,
            )
        ).first()
        return row is not None

    return bool(_read(chat_store, "select_chat_thread_by_session", read))


def _mirror_linked_status(
    store: ChatStore,
    session_id: str,
    status: str,
    response_id: str | None,
    error_code: str | None,
) -> None:
    """Copy an idle assistant reply, or a failed turn, into the linked thread."""
    try:
        if status == "idle":
            if not response_id:
                return
            text = _assistant_text(session_id)
            if not text:
                return
            _mirror(
                store,
                session_id,
                response_id,
                body=_truncate_mirror(text),
                author_kind="omnigent_agent",
                author_id=_AUTHOR_AGENT,
                system_code=None,
            )
            return
        reason = error_code if error_code in _REASON_CLASSES else "internal"
        _mirror(
            store,
            session_id,
            response_id or "failed",
            body=f"Agent-Fehler: {reason}",
            author_kind="system",
            author_id=_AUTHOR_SYSTEM,
            system_code=reason,
        )
    except Exception:
        _logger.exception("agent thread status mirror failed session=%s", session_id)


def _run_status_work(work: Callable[[], None]) -> None:
    """Run ``work`` inline when status publish is already off the event loop.

    On the loop, schedule it and return so the publish is not blocked. A
    worker that is already off the loop runs it inline.
    """

    def guarded() -> None:
        with _status_gate:
            work()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        guarded()
        return
    ctx = contextvars.copy_context()
    job = loop.run_in_executor(None, ctx.run, guarded)
    _status_jobs.add(job)
    job.add_done_callback(_status_jobs.discard)


def on_session_status(
    session_id: str,
    status: str,
    response_id: str | None = None,
    error_code: str | None = None,
) -> None:
    """Mirror an idle assistant reply, or a failed turn, into its agent thread.

    Called from the session status publisher. A missing chat thread, a missing
    response id, or a store error leaves the status publish itself alone.
    The assistant summary runs only after a thread row matches ``session_id``.
    """
    store = _bound_chat_store
    if store is None or status not in ("idle", "failed"):
        return
    if status == "idle" and not response_id:
        return
    try:
        linked = _session_has_thread(store, session_id)
    except Exception:
        _logger.exception("agent thread status mirror failed session=%s", session_id)
        return
    if not linked:
        return

    def work() -> None:
        _mirror_linked_status(store, session_id, status, response_id, error_code)

    _run_status_work(work)
