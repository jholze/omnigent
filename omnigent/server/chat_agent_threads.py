"""Start one Omnigent session for a human ``<@omnigent_agent:agent>`` mention.

The thread row links that session. A later idle status copies the final
assistant text back into the thread. Message bodies are never logged.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
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
from omnigent.server.chat_access import principal_kind, require_read
from omnigent.server.chat_mentions import parse_mentions
from omnigent.server.chat_realtime import publish_chat, publish_message_created
from omnigent.server.principals import same_account
from omnigent.stores.chat_store import ChatChannel, ChatMessage, ChatStore, PostStatus

_logger = logging.getLogger("omnigent.chat")

_HANDLE = "agent"
_DISMISS_COMMAND = "/dismiss"
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

# custom-lint: disable-next=workspace-scoped-cache -- one maker per database URI
_makers: dict[str, Any] = {}
_bound_chat_store: ChatStore | None = None
# custom-lint: disable-next=workspace-scoped-cache -- task objects
_pending: set[asyncio.Task[None]] = set()
_timeout_task: asyncio.Task[None] | None = None
# custom-lint: disable-next=workspace-scoped-cache -- reason-code counters, not tenant data
_result_counts: dict[str, int] = {}
# Mirrors scheduled off the event loop. The status publish returns first.
# custom-lint: disable-next=workspace-scoped-cache -- task objects
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


def slugify_agent_handle(name: str) -> str:
    """Slugify an agent name for its handle (FR-039).

    Lowercases, collapses runs of non-alphanumerics to one hyphen, and trims
    leading/trailing hyphens. Unlike the ACP slug, an empty result does not
    fall back to ``agent``: that handle is reserved for the project-agent
    alias (FR-038).
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "unnamed"


def visible_agent_handles(agent_store: Any, caller: str) -> list[tuple[str, str, str]]:
    """Return ``(handle, agent_id, name)`` for every agent the caller sees.

    Visibility matches ``GET /v1/agents``: server agents from
    ``agent_store.list()`` plus the caller's own user agents. Handles are
    unique and assigned in ``created_at`` ascending order; the oldest agent
    keeps the bare slug and later collisions get ``-2``, ``-3`` … (FR-039).
    The reserved ``agent`` handle is never taken (FR-038).
    """
    agents: list[Any] = list(agent_store.list(limit=1000).data)
    if getattr(agent_store, "supports_user_agents", False):
        agents.extend(agent_store.list_user_agents(caller, limit=50).data)
    ordered = sorted(agents, key=lambda agent: (agent.created_at, agent.id))
    used: set[str] = {_HANDLE}
    entries: list[tuple[str, str, str]] = []
    for agent in ordered:
        base = slugify_agent_handle(agent.name)
        candidate = base
        suffix = 1
        while candidate in used:
            suffix += 1
            candidate = f"{base}-{suffix}"
        used.add(candidate)
        entries.append((candidate, agent.id, agent.name))
    return entries


def request_agent_store(request: Any) -> Any:
    """Return the request's agent store, falling back to the runtime store."""
    from omnigent.runtime import get_agent_store

    agent_store = getattr(request.app.state, "agent_store", None)
    if agent_store is None:
        agent_store = get_agent_store()
    return agent_store


def _agent_handle_mentions(body: str) -> list[str]:
    """Return the ``omnigent_agent`` handles mentioned in *body*, in order."""
    return [token.target_id for token in parse_mentions(body) if token.kind == "omnigent_agent"]


def _visible_handle(request: Any, caller: str, handles: list[str]) -> bool:
    """Whether one of *handles* is addressable by *caller*.

    ``agent`` is the project-agent alias (FR-038) and is always addressable.
    Any other handle is addressable only when it appears in the caller's
    ``GET /v1/chat/agents`` listing (FR-039).
    """
    if any(handle == _HANDLE for handle in handles):
        return True
    visible = {entry[0] for entry in visible_agent_handles(request_agent_store(request), caller)}
    return any(handle in visible for handle in handles)


def _human_label(principal_id: str) -> str:
    """Email local-part label for a human principal, e.g. ``jens@…`` → ``Jens``."""
    local = principal_id.split("@", 1)[0].strip() or principal_id
    return local[:1].upper() + local[1:]


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
    return _human_label(principal_id)


def _record(
    reason: str,
    *,
    channel_id: str,
    message_id: str,
    account_id: str,
    owner_id: str,
    session_id: str | None,
) -> None:
    """Count one start outcome. The row names the class, never the message."""
    _result_counts[reason] = _result_counts.get(reason, 0) + 1
    add_audit_attrs(
        action="agent_thread_start",
        channel_id=channel_id,
        message_id=message_id,
        account_id=account_id,
        owner_id=owner_id,
        result=reason,
        outcome=reason,
    )
    _logger.info(
        "agent thread start result=%s count=%s",
        reason,
        _result_counts[reason],
        extra=debug_event(
            "agent_thread_start",
            session_id=session_id,
            user_id=owner_id,
            result=reason,
            channel_id=channel_id,
            message_id=message_id,
            count=_result_counts[reason],
        ),
    )


def _claim(
    chat_store: ChatStore,
    channel_id: str,
    root_id: str,
    caller: str,
    started_by: str | None = None,
) -> bool:
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
                    started_by_agent_account_id=started_by,
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
                started_by_agent_account_id=started_by,
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
    account_id: str,
    owner_id: str,
    reason: str,
) -> None:
    """Mark a still-starting thread failed and post one classed system line.

    *account_id* is the bot account when a bot started the thread, otherwise
    the author's principal. *owner_id* is the human who would own the session.
    """
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
    _record(
        reason,
        channel_id=channel_id,
        message_id=message_id,
        account_id=account_id,
        owner_id=owner_id,
        session_id=None,
    )


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


def _caller_runner_id(app: Any, caller: str) -> str | None:
    """First online runner the caller owns; None when no runner is online.

    Online runners that all belong to someone else, or to no one, fail the start.
    """
    registry = getattr(app.state, "tunnel_registry", None)
    runner_router = getattr(app.state, "runner_router", None)
    if registry is None or runner_router is None:
        return None
    online = registry.online_runner_ids()
    if not online:
        return None
    for runner_id in online:
        if same_account(caller, runner_router.runner_owner(runner_id)):
            return runner_id
    raise _StartFailure("host_offline")


def _pin_caller_runner(
    app: Any, conversation_store: Any, caller: str, session_id: str, runner_id: str
) -> None:
    """Pin the new session to the caller's runner, or fail when another runner won."""
    if conversation_store.set_runner_id(session_id, runner_id):
        return
    conversation = conversation_store.get_conversation(session_id)
    pinned = getattr(conversation, "runner_id", None)
    runner_router = getattr(app.state, "runner_router", None)
    if pinned is None or runner_router is None:
        raise _StartFailure("host_offline")
    if not same_account(caller, runner_router.runner_owner(pinned)):
        raise _StartFailure("host_offline")


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


def _sharing_forbids_grants(sharing: SharingMode, workspace: str | None) -> bool:
    """Whether the sharing mode blocks a new session grant (FR-034, FR-086)."""
    return sharing == SharingMode.OFF or (
        sharing == SharingMode.RESTRICTED_READ_ONLY and workspace_sharing_blocked(workspace)
    )


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


def grant_join_read(
    request: Any,
    chat_store: ChatStore,
    channel_id: str,
    caller: str,
) -> None:
    """Grant READ to a joining human on this channel's agent-thread sessions.

    Reuses the sharing-mode gate from session start (FR-034): no grant when
    sharing is OFF, or when RESTRICTED_READ_ONLY and the session's workspace
    is blocked (FR-086). Leaving withdraws nothing (A-20), so this grant is
    never revoked here. One failure does not abort the rest.
    """
    if principal_kind(caller) != "user":
        return
    sharing = _sharing_mode(request)
    if sharing == SharingMode.OFF:
        return
    from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
    from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore

    permission_store = SqlAlchemyPermissionStore(chat_store.storage_location)
    conversation_store = SqlAlchemyConversationStore(chat_store.storage_location)
    for thread in chat_store.list_channel_agent_threads(channel_id):
        session_id = thread.session_id
        if not session_id:
            continue
        try:
            if sharing == SharingMode.RESTRICTED_READ_ONLY:
                conversation = conversation_store.get_conversation(session_id)
                workspace = conversation.workspace if conversation is not None else None
                if workspace_sharing_blocked(workspace):
                    continue
            if permission_store.get_permission_level(caller, session_id) is None:
                permission_store.ensure_user(caller)
                permission_store.grant(caller, session_id, LEVEL_READ)
        except Exception:
            _logger.exception(
                "agent thread join read grant failed session=%s member=%s",
                session_id,
                caller,
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
    from omnigent.server.mcp_chat import untrusted_text
    from omnigent.server.schemas import SessionEventInput

    endpoint = _find_post_event(request.app)
    if endpoint is None:
        raise _StartFailure("internal")
    # Chat text is data. The same wrapper chat_read uses stops a body from
    # closing the marker or being read as instructions (FR-095).
    result = await endpoint(
        request,
        session_id,
        SessionEventInput(
            type="message",
            data={
                "role": "user",
                "content": [{"type": "input_text", "text": untrusted_text({"text": text})}],
            },
        ),
    )
    if isinstance(result, dict) and result.get("denied"):
        raise _StartFailure("forbidden")
    if isinstance(result, dict) and result.get("queued") is False:
        raise _StartFailure("internal")


def _bot_project_allowed(chat_store: ChatStore, account_id: str, project_id: str) -> bool:
    """Whether a bot may start a session in *project_id* (ADR-0011).

    An unscoped account follows its owner. A scoped account needs the project
    in its allow-list; a missing or revoked account is refused.
    """
    account = chat_store.get_agent_account_by_principal(f"agent:{account_id}")
    if account is None:
        return False
    project_ids = getattr(account, "project_ids", None)
    if project_ids is None or project_id in project_ids:
        return True
    from omnigent.server.agent_project_scope import ProjectScope, record_scope_denial

    record_scope_denial(
        "project_out_of_scope",
        ProjectScope(account_id=account.id, project_ids=frozenset(project_ids)),
    )
    return False


def _request_as_owner(request: Any) -> Any:
    """Clone the request so in-process auth acts as the bot's owner.

    The flag is set on this scope copy only. The original request, and any
    client header, stay unchanged. Accounts mode caches the authenticated
    principal on the scope; leaving the bot's entry there makes
    ``get_user_id`` return ``agent:<id>`` and the session edit check fails.
    """
    from starlette.requests import Request

    from omnigent.server.auth import _CONNECTION_IDENTITY_KEY

    scope = dict(request.scope)
    scope["mcp_inprocess"] = True
    scope.pop(_CONNECTION_IDENTITY_KEY, None)
    return Request(scope)


async def _start_impl(
    request: Any,
    chat_store: ChatStore,
    channel: ChatChannel,
    message: ChatMessage,
    root_id: str,
    caller: str | None = None,
    started_by: str | None = None,
) -> None:
    """Create the caller's session, grant access, and send the thread context.

    A bot mention passes the owner as *caller* and the account id as
    *started_by*. The session owner is that human.
    """
    from omnigent.server.routes._sessions.orchestration import _create_session_from_existing_agent
    from omnigent.server.schemas import ProjectSessionCreateRequest

    caller = caller or message.author_id
    acting = _request_as_owner(request) if caller != message.author_id else request
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
    if started_by is not None and not await asyncio.to_thread(
        _bot_project_allowed, chat_store, started_by, project.id
    ):
        raise _StartFailure("forbidden")
    handles = _agent_handle_mentions(message.body)
    if handles and not await asyncio.to_thread(_visible_handle, request, caller, handles):
        raise _StartFailure("agent_not_found")
    host_id = _live_host_id(host_store, caller, project)
    runner_id = _caller_runner_id(request.app, caller) if host_id is None else None
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
        getattr(acting.app.state, "runner_router", None),
        body,
        acting,
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
    if _sharing_forbids_grants(sharing, workspace):
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
    if runner_id is not None:
        await asyncio.to_thread(
            _pin_caller_runner, request.app, conversation_store, caller, session_id, runner_id
        )
    text = await asyncio.to_thread(_context_text, chat_store, channel.id, root_id, message)
    await _post_first_message(acting, session_id, text)
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
    if started_by:
        await asyncio.to_thread(
            _post_started_by, chat_store, channel.id, root_id, started_by, caller
        )
    _record(
        "active",
        channel_id=channel.id,
        message_id=message.id,
        account_id=started_by or caller,
        owner_id=caller,
        session_id=session_id,
    )


async def _run_start(
    request: Any,
    chat_store: ChatStore,
    channel: ChatChannel,
    message: ChatMessage,
    root_id: str,
    caller: str | None = None,
    started_by: str | None = None,
) -> None:
    """Run one claimed start and collapse every failure into a system line."""
    try:
        await _start_impl(
            request,
            chat_store,
            channel,
            message,
            root_id,
            caller,
            started_by,
        )
    except _StartFailure as exc:
        await asyncio.to_thread(
            _fail,
            chat_store,
            channel_id=channel.id,
            root_id=root_id,
            message_id=message.id,
            account_id=started_by or message.author_id,
            owner_id=caller or message.author_id,
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
            account_id=started_by or message.author_id,
            owner_id=caller or message.author_id,
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
            account_id=started_by or message.author_id,
            owner_id=caller or message.author_id,
            reason="internal",
        )


def _post_started_by(
    chat_store: ChatStore,
    channel_id: str,
    root_id: str,
    account_id: str,
    owner_id: str,
) -> None:
    """System line naming the bot and the owner who owns the session."""

    def write(session: Session) -> _Notice:
        account = session.get(SqlChatAgentAccount, (current_workspace_id(), account_id))
        bot_name = account.display_name.strip() if account is not None else "Bot"
        owner_name = _display_in(session, owner_id)
        return _insert_reply(
            session,
            channel_id=channel_id,
            root_id=root_id,
            author_id=_AUTHOR_SYSTEM,
            author_kind="system",
            body=f"gestartet von {bot_name} ({owner_name})",
            system_code="started_by_agent",
        )

    _emit(chat_store, _write(chat_store, "insert_started_by_notice", write))


def _quota_blocks(chat_store: ChatStore, account_id: str) -> bool:
    """Whether this account is at its configured 24 h session cap."""
    from omnigent.server.chat_rate_limit import bot_session_quota_exceeded, quota_window_start_us

    now = now_epoch_us()
    persisted = chat_store.count_sessions_started_by(account_id, quota_window_start_us(now))
    return bot_session_quota_exceeded(account_id, persisted, now_us=now)


def _post_quota_notice(chat_store: ChatStore, channel: ChatChannel, message: ChatMessage) -> None:
    """Tell the thread the bot's session quota is exhausted. No session starts."""
    account = chat_store.get_agent_account_by_principal(message.author_id)
    if account is not None:
        _record(
            "refused/quota",
            channel_id=channel.id,
            message_id=message.id,
            account_id=account.id,
            owner_id=account.owner_id,
            session_id=None,
        )
    result = chat_store.post_message(
        channel.id,
        _AUTHOR_SYSTEM,
        "Bot-Kontingent erschöpft",
        author_kind="system",
        thread_root_id=message.thread_root_id or message.id,
    )
    if result.status is PostStatus.CREATED and result.message is not None:
        publish_message_created(chat_store, channel, result.message)


def _set_forward_state(
    chat_store: ChatStore,
    message_id: str,
    state: str,
    *,
    expected: str | None = None,
) -> bool:
    """Store a reply's forward state; with *expected*, only from that state."""

    def write(session: Session) -> bool:
        statement = update(SqlChatMessage).where(
            SqlChatMessage.workspace_id == current_workspace_id(),
            SqlChatMessage.id == message_id,
        )
        if expected is not None:
            statement = statement.where(SqlChatMessage.forward_state == expected)
        changed = cast(CursorResult[Any], session.execute(statement.values(forward_state=state)))
        return changed.rowcount == 1

    return bool(_write(chat_store, "set_chat_forward_state", write))


@dataclass(frozen=True)
class _ThreadLink:
    """The agent columns of one thread row."""

    channel_id: str
    agent_state: str
    session_id: str | None
    caller_id: str | None


def _thread_link(chat_store: ChatStore, root_id: str) -> _ThreadLink | None:
    """Read the agent state and session link of a thread."""

    def read(session: Session) -> _ThreadLink | None:
        row = session.get(SqlChatThread, (current_workspace_id(), root_id))
        if row is None:
            return None
        return _ThreadLink(
            channel_id=row.channel_id,
            agent_state=row.agent_state,
            session_id=row.session_id,
            caller_id=row.caller_id,
        )

    link = _read(chat_store, "select_agent_thread_link", read)
    return link if isinstance(link, _ThreadLink) else None


def _set_agent_state(chat_store: ChatStore, root_id: str, source: str, target: str) -> bool:
    """Move a thread from *source* to *target* state, once."""

    def write(session: Session) -> bool:
        changed = cast(
            CursorResult[Any],
            session.execute(
                update(SqlChatThread)
                .where(
                    SqlChatThread.workspace_id == current_workspace_id(),
                    SqlChatThread.root_message_id == root_id,
                    SqlChatThread.agent_state == source,
                )
                .values(agent_state=target, updated_at=now_epoch_us())
            ),
        )
        return changed.rowcount == 1

    return bool(_write(chat_store, "set_agent_thread_state", write))


async def _acting_principal(chat_store: ChatStore, principal_id: str) -> str | None:
    """The human whose session level governs *principal_id*; a bot acts as its owner."""
    if principal_kind(principal_id) != "agent_account":
        return principal_id
    account = await asyncio.to_thread(chat_store.get_agent_account_by_principal, principal_id)
    return account.owner_id if account is not None else None


async def _session_level(chat_store: ChatStore, principal_id: str, session_id: str) -> int:
    """Effective level of *principal_id* on *session_id*; 0 when none."""
    from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore

    level = await asyncio.to_thread(
        SqlAlchemyPermissionStore(chat_store.storage_location).get_permission_level,
        principal_id,
        session_id,
    )
    return level if isinstance(level, int) else 0


async def _forward_reply(
    request: Any,
    chat_store: ChatStore,
    message: ChatMessage,
    session_id: str,
    requester: str,
) -> str:
    """Forward one thread reply to *session_id* and store the outcome.

    A human reply is sent as ``[Name] text``, a bot reply as
    ``[Bot (Owner)] text`` with the owner's rights. Below EDIT the reply is
    ``not_forwarded``; a failed delivery is ``failed`` so it can be retried.
    *requester* made *request*. Returns the stored state.
    """
    from omnigent.server.auth import LEVEL_EDIT

    account = None
    principal = message.author_id
    if principal_kind(message.author_id) == "agent_account":
        account = await asyncio.to_thread(
            chat_store.get_agent_account_by_principal, message.author_id
        )
        if account is None:
            await asyncio.to_thread(_set_forward_state, chat_store, message.id, "not_forwarded")
            return "not_forwarded"
        principal = account.owner_id
    if await _session_level(chat_store, principal, session_id) < LEVEL_EDIT:
        await asyncio.to_thread(_set_forward_state, chat_store, message.id, "not_forwarded")
        return "not_forwarded"
    name = await asyncio.to_thread(
        _read,
        chat_store,
        "select_forward_display",
        lambda session: _display_in(session, principal),
    )
    if account is not None:
        name = f"{account.display_name} ({name})"
    acting = request
    if principal_kind(requester) == "agent_account":
        acting = _request_as_owner(request)
    try:
        await _post_first_message(acting, session_id, f"[{name}] {message.body}")
    except Exception:
        _logger.exception(
            "thread reply forward failed session=%s message=%s", session_id, message.id
        )
        await asyncio.to_thread(_set_forward_state, chat_store, message.id, "failed")
        return "failed"
    await asyncio.to_thread(_set_forward_state, chat_store, message.id, "forwarded")
    return "forwarded"


async def _follow_reply(request: Any, chat_store: ChatStore, message: ChatMessage) -> None:
    """Forward a reply in an active thread to that thread's session."""
    if message.thread_root_id is None:
        return
    link = await asyncio.to_thread(_thread_link, chat_store, message.thread_root_id)
    if link is None or link.agent_state != "active" or not link.session_id:
        return
    await _forward_reply(request, chat_store, message, link.session_id, message.author_id)


async def _follow_mention(request: Any, chat_store: ChatStore, message: ChatMessage) -> None:
    """Send an agent mention to the thread's session, reactivating a dismissed thread.

    Only a human with EDIT reactivates. Any other mention in a dismissed
    thread stays in chat and is marked ``not_forwarded``.
    """
    from omnigent.server.auth import LEVEL_EDIT

    root_id = message.thread_root_id
    if root_id is None:
        return
    link = await asyncio.to_thread(_thread_link, chat_store, root_id)
    if link is None or not link.session_id:
        return
    if link.agent_state == "dismissed":
        allowed = principal_kind(message.author_id) == "user" and (
            await _session_level(chat_store, message.author_id, link.session_id) >= LEVEL_EDIT
        )
        if not allowed:
            await asyncio.to_thread(_set_forward_state, chat_store, message.id, "not_forwarded")
            return
        if await asyncio.to_thread(_set_agent_state, chat_store, root_id, "dismissed", "active"):
            _audit_thread_state("agent_thread_reactivate", link, root_id, message.author_id)
            await asyncio.to_thread(_publish_thread, chat_store, link.channel_id, root_id)
        link = await asyncio.to_thread(_thread_link, chat_store, root_id)
    if link is None or link.agent_state != "active" or not link.session_id:
        return
    await _forward_reply(request, chat_store, message, link.session_id, message.author_id)


def _audit_thread_state(action: str, link: _ThreadLink, root_id: str, actor: str) -> None:
    """Record a dismiss or reactivation without any message body."""
    add_audit_attrs(
        action=action,
        channel_id=link.channel_id,
        root_message_id=root_id,
        actor_id=actor,
        outcome="ok",
    )
    _logger.info(
        "%s thread=%s session=%s",
        action,
        root_id,
        link.session_id,
        extra=debug_event(
            action,
            session_id=link.session_id,
            user_id=actor,
            channel_id=link.channel_id,
        ),
    )


def is_dismiss_command(body: str) -> bool:
    """Whether a thread reply is the ``/dismiss`` command."""
    return body.strip().lower() == _DISMISS_COMMAND


async def dismiss_agent_thread(
    chat_store: ChatStore,
    root_id: str,
    principal_id: str,
    *,
    channel_id: str | None = None,
) -> _ThreadLink | None:
    """Dismiss the agent of an active thread without stopping its session.

    The thread caller and any principal with EDIT on the session may
    dismiss; a bot acts as its owner. Returns ``None`` when the thread (in
    *channel_id*, if given) has no agent to dismiss. An already dismissed
    thread is returned unchanged.

    :raises OmnigentError: 403 ``forbidden`` for anyone else.
    """
    from omnigent.server.auth import LEVEL_EDIT

    link = await asyncio.to_thread(_thread_link, chat_store, root_id)
    if link is None or (channel_id is not None and link.channel_id != channel_id):
        return None
    if link.agent_state not in ("active", "dismissed") or not link.session_id:
        return None
    acting = await _acting_principal(chat_store, principal_id)
    allowed = acting is not None and (
        acting == link.caller_id
        or await _session_level(chat_store, acting, link.session_id) >= LEVEL_EDIT
    )
    if not allowed:
        raise OmnigentError(
            "only the caller or an editor of the session may dismiss the agent",
            code=ErrorCode.FORBIDDEN,
        )
    if link.agent_state == "active" and await asyncio.to_thread(
        _set_agent_state, chat_store, root_id, "active", "dismissed"
    ):
        _audit_thread_state("agent_thread_dismiss", link, root_id, principal_id)
        await asyncio.to_thread(_publish_thread, chat_store, link.channel_id, root_id)
    return await asyncio.to_thread(_thread_link, chat_store, root_id)


def _load_message(chat_store: ChatStore, message_id: str) -> tuple[ChatMessage, str | None] | None:
    """Return a live message and its forward state."""

    def read(session: Session) -> tuple[ChatMessage, str | None] | None:
        row = session.get(SqlChatMessage, (current_workspace_id(), message_id))
        if row is None or row.deleted_at is not None:
            return None
        message = ChatMessage(
            id=row.id,
            channel_id=row.channel_id,
            thread_root_id=row.thread_root_id,
            author_id=row.author_id,
            author_kind=row.author_kind,
            created_at=row.created_at,
            body=row.body,
            client_id=row.client_id,
        )
        return message, row.forward_state

    found = _read(chat_store, "select_chat_message_forward", read)
    return found if isinstance(found, tuple) else None


async def retry_forward(
    request: Any,
    chat_store: ChatStore,
    message_id: str,
    principal_id: str,
) -> str:
    """Retry one failed forward and return the stored ``forward_state``.

    Only the author, or a bot author's owner, may retry. A message that is
    already ``forwarded`` is returned as is and never sent again.

    :raises OmnigentError: 404 when the message is missing or unreadable,
        403 for other callers, 409 when it is not awaiting a retry or the
        thread agent is no longer active.
    """
    found = await asyncio.to_thread(_load_message, chat_store, message_id)
    if found is None:
        raise OmnigentError("Message not found", code=ErrorCode.NOT_FOUND)
    message, state = found
    channel = await asyncio.to_thread(chat_store.get_channel, message.channel_id)
    if channel is None:
        raise OmnigentError("Message not found", code=ErrorCode.NOT_FOUND)
    await asyncio.to_thread(require_read, chat_store, channel, principal_id)
    acting = await _acting_principal(chat_store, principal_id)
    author_owner = await _acting_principal(chat_store, message.author_id)
    if principal_id != message.author_id and (acting is None or acting != author_owner):
        raise OmnigentError("only the author may retry this forward", code=ErrorCode.FORBIDDEN)
    if state == "forwarded":
        return state
    if state != "failed" or message.thread_root_id is None:
        raise OmnigentError("message is not awaiting a forward retry", code=ErrorCode.CONFLICT)
    link = await asyncio.to_thread(_thread_link, chat_store, message.thread_root_id)
    if link is None or link.agent_state != "active" or not link.session_id:
        raise OmnigentError("thread agent is not active", code=ErrorCode.CONFLICT)
    claimed = await asyncio.to_thread(
        _set_forward_state, chat_store, message.id, "forwarding", expected="failed"
    )
    if not claimed:
        current = await asyncio.to_thread(_load_message, chat_store, message.id)
        if current is not None and current[1] == "forwarded":
            return "forwarded"
        raise OmnigentError("message is not awaiting a forward retry", code=ErrorCode.CONFLICT)
    return await _forward_reply(request, chat_store, message, link.session_id, principal_id)


async def schedule_after_human_post(
    request: Any,
    chat_store: ChatStore,
    channel: ChatChannel,
    message: ChatMessage,
) -> None:
    """Claim and start an agent thread after a mention, as the human owner.

    A human mention starts as that human. An agent-account mention starts as
    the account's owner and records the account as initiator. A mention whose
    handle the caller cannot see fails as ``agent_not_found`` without starting
    a session (FR-039). Any reply in an active thread is forwarded when its
    author (or a bot's owner) has edit access. An agent mention reactivates a
    dismissed thread; a thread that is still starting is left alone.
    """
    try:
        _bind_store(chat_store)
        _ensure_timeout_loop(chat_store)
        handles = _agent_handle_mentions(message.body)
        if not handles:
            await _follow_reply(request, chat_store, message)
            return
        kind = principal_kind(message.author_id)
        started_by: str | None = None
        if kind == "user":
            caller = message.author_id
        elif kind == "agent_account":
            account = await asyncio.to_thread(
                chat_store.get_agent_account_by_principal, message.author_id
            )
            if account is None:
                return
            if await asyncio.to_thread(_quota_blocks, chat_store, account.id):
                await asyncio.to_thread(_post_quota_notice, chat_store, channel, message)
                return
            caller = account.owner_id
            started_by = account.id
        else:
            return
        root_id = message.thread_root_id or message.id
        claimed = await asyncio.to_thread(
            _claim, chat_store, channel.id, root_id, caller, started_by
        )
        if not claimed:
            await _follow_mention(request, chat_store, message)
            return
        await asyncio.to_thread(_publish_thread, chat_store, channel.id, root_id)
        task = asyncio.create_task(
            _run_start(request, chat_store, channel, message, root_id, caller, started_by)
        )
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
                SqlChatThread.started_by_agent_account_id,
            ).where(
                SqlChatThread.workspace_id == current_workspace_id(),
                SqlChatThread.agent_state == "starting",
                SqlChatThread.start_requested_at.is_not(None),
                SqlChatThread.start_requested_at < cutoff,
            )
        ).all()
        notices: list[_Notice] = []
        for root_id, channel_id, caller_id, started_by in rows:
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
            owner = caller_id or _AUTHOR_SYSTEM
            _record(
                "timeout",
                channel_id=channel_id,
                message_id=root_id,
                account_id=started_by or owner,
                owner_id=owner,
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
