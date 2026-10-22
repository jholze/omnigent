"""REST routes for team chat, mounted under ``/v1/chat``.

Every route is gated on ``Feature.TEAM_CHAT`` before any store read or
write: with the flag off each ``/v1/chat/*`` request answers 404
(FR-003). Store results are mapped to HTTP here — the store itself
speaks typed results, not status codes.
"""

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from omnigent.db.utils import now_epoch_us
from omnigent.debug_logging import add_audit_attrs
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.agent_account_tokens import (
    agent_account_secret,
    derive_webhook_secret,
    webhook_url_rejected,
)
from omnigent.server.auth import RESERVED_USER_LOCAL, AuthProvider
from omnigent.server.chat_access import (
    author_principal_type,
    principal_kind,
    require_owner_membership,
    require_post,
    require_read,
)
from omnigent.server.chat_agent_threads import (
    _human_label,
    dismiss_agent_thread,
    is_dismiss_command,
    retry_forward,
)
from omnigent.server.chat_rate_limit import consume_agent_request, consume_channel_post
from omnigent.server.chat_realtime import publish_message_created, publish_read_updated
from omnigent.server.feature_flags import Feature, FeatureFlags, resolve_feature_flags
from omnigent.server.routes._auth_helpers import require_user
from omnigent.stores.chat_store import (
    AccountStatus,
    ChannelAgentThread,
    ChatAgentAccount,
    ChatChannel,
    ChatMessage,
    ChatStore,
    DmStatus,
    PostStatus,
    ReadStatus,
)

_GENERAL_CHANNEL = "general"
_MAX_BODY_CHARS = 4000
_MIN_DM_OTHERS = 1
_MAX_DM_OTHERS = 8
_DEFAULT_MESSAGES_LIMIT = 50
_MAX_MESSAGES_LIMIT = 200
_CHANGES_LIMIT = 1000
_CHANNEL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_UNREAD_LIMIT = 50
_ACTIVE_WINDOW_US = 10 * 60 * 1_000_000


class ChannelResponse(BaseModel):
    """Wire shape of a channel, DM, or session-chat container."""

    id: str
    kind: str
    name: str | None
    topic: str | None
    project_name: str | None
    visibility: str
    created_by: str | None
    created_at: int
    unread_count: int = 0
    mention_count: int = 0


class MessageResponse(BaseModel):
    """Wire shape of one stored message.

    ``agent_state`` is read-only and present only on thread-root
    messages (``thread_root_id is None``); replies omit it. It mirrors
    ``SqlChatThread.agent_state`` (``none``/``starting``/``active``/
    ``dismissed``/``start_failed``/``closed``) so the UI can render
    the agent thread's start state without a separate thread fetch.

    ``session_id`` follows the same rule: present only on thread-root
    messages whose thread has an agent session, omitted otherwise. It
    is the value ``thread.updated`` already sends to every channel
    member, so a reloaded client can mount the live view.
    """

    id: str
    channel_id: str
    thread_root_id: str | None
    author_id: str
    author_kind: str
    created_at: int
    body: str
    client_id: str | None
    agent_state: str | None = None
    session_id: str | None = None


class DirectoryEntry(BaseModel):
    """One mentionable principal in the bootstrap directory."""

    id: str
    kind: str
    display_name: str | None = None
    handle: str | None = None
    owner_id: str | None = None
    """Owning human of an agent account; ``None`` for users."""


class BootstrapResponse(BaseModel):
    """``GET /v1/chat/bootstrap``."""

    me: str
    channels: list[ChannelResponse]
    dms: list[ChannelResponse]
    directory: list[DirectoryEntry]
    changes_cursor: str


class MessagePageResponse(BaseModel):
    """``GET /v1/chat/channels/{id}/messages``."""

    items: list[MessageResponse]
    has_older: bool
    has_more: bool
    next_after: str | None
    next_before: str | None


class ChannelAgentThreadResponse(BaseModel):
    """One active agent thread the caller may already read.

    ``session_id`` is omitted until the thread has a session. Channel
    members already receive that id from ``thread.updated`` and from
    the message page. ``body`` is the root message text, omitted when
    that row is missing or deleted.
    """

    root_message_id: str
    agent_state: str
    session_id: str | None = None
    body: str | None = None


class ForwardResponse(BaseModel):
    """``POST /v1/chat/messages/{id}/forward``: the reply's stored forward state."""

    id: str
    forward_state: str


class ChangeItem(BaseModel):
    """One entry in the change feed."""

    kind: str
    id: str
    channel_id: str
    thread_root_id: str | None
    created_at: int


class ChangesResponse(BaseModel):
    """``GET /v1/chat/changes``."""

    items: list[ChangeItem]
    next: str | None
    reset: bool


class ChannelCreateBody(BaseModel):
    """``POST /v1/chat/channels``."""

    name: str
    topic: str | None = None
    project_name: str | None = None


class MessagePostBody(BaseModel):
    """``POST /v1/chat/channels/{id}/messages``.

    Extra fields are ignored, so a client-supplied ``created_by`` cannot
    override the server-set author.
    """

    body: str
    client_id: str | None = None
    thread_root_id: str | None = None


class DmCreateBody(BaseModel):
    """``POST /v1/chat/dms`` — the other participants, excluding the caller."""

    principals: list[str]


class MemberResponse(BaseModel):
    """One channel member, including the principal id bots mention."""

    principal_id: str
    principal_type: str
    role: str
    joined_at: int
    kind: str
    display: str
    status: str | None = None


class AuthorResponse(BaseModel):
    """Author of an unread item. ``kind`` includes ``system`` (FR-095)."""

    id: str
    kind: str
    display: str


class UnreadItemResponse(BaseModel):
    """One unread mention or DM message for an agent account (FR-088)."""

    channel_id: str
    thread_root_id: str | None
    message_id: str
    author: AuthorResponse
    body: str
    created_at: int
    reason: str
    content_trust: str = "untrusted"


class UnreadResponse(BaseModel):
    """``GET /v1/chat/unread``."""

    items: list[UnreadItemResponse]
    next_cursor: str | None = None


class ReadBody(BaseModel):
    """``POST /v1/chat/channels/{id}/read``.

    ``id`` is the message id. ``message_id`` is accepted as the same field.
    ``created_at`` must match that message when it is sent.
    """

    id: str | None = None
    message_id: str | None = None
    created_at: int | None = None
    thread_root_id: str | None = None


class AgentAccountBody(BaseModel):
    """``POST /v1/chat/agent-accounts``."""

    display_name: str
    handle: str
    webhook_url: str | None = None


class AgentAccountResponse(BaseModel):
    """An agent account. The raw token is not repeated on this object."""

    id: str
    principal_id: str
    owner_id: str
    display_name: str
    handle: str
    token_prefix: str
    secret_version: int
    # Epoch microseconds of the last authenticated call. Null if never seen.
    last_seen_at: int | None = None


class AgentAccountCreatedResponse(BaseModel):
    """``POST /v1/chat/agent-accounts``. Token and webhook secret are shown once."""

    account: AgentAccountResponse
    token: str
    webhook_secret: str


class AgentAccountRotatedResponse(BaseModel):
    """``POST /v1/chat/agent-accounts/{id}/rotate``. The previous token is dead."""

    account: AgentAccountResponse
    token: str
    webhook_secret: str


def _resolve_caller(request: Request, auth_provider: AuthProvider | None) -> str:
    """Resolve the caller principal, falling back to the local user.

    An in-process call from ``/mcp`` authenticates as the owner. Chat
    authorship, membership, and the streak still use the agent account,
    which the auth branch stored on the scope as ``mcp_actor``.

    :raises OmnigentError: 401 when multi-user auth is active but the
        request carries no identity.
    """
    actor = request.scope.get("mcp_actor")
    if request.scope.get("mcp_inprocess") is True and isinstance(actor, str) and actor:
        return actor
    user_id = require_user(request, auth_provider)
    return user_id if user_id is not None else RESERVED_USER_LOCAL


def _parse_cursor(raw: str | None) -> tuple[int, str] | None:
    """Parse an exclusive ``created_at:id`` cursor.

    :param raw: Query value; ``None`` or ``""`` means "no cursor".
    :raises OmnigentError: 422 ``invalid_cursor`` when malformed.
    """
    if raw is None or raw == "":
        return None
    created_raw, separator, message_id = raw.partition(":")
    if separator != ":" or not created_raw.isdigit() or not message_id:
        raise OmnigentError("cursor must be created_at:id", code=ErrorCode.INVALID_CURSOR)
    return int(created_raw), message_id


def _channel_response(
    channel: ChatChannel, *, unread_count: int = 0, mention_count: int = 0
) -> ChannelResponse:
    """Map a stored channel onto its wire shape."""
    return ChannelResponse(
        id=channel.id,
        kind=channel.kind,
        name=channel.name,
        topic=channel.topic,
        project_name=channel.project_name,
        visibility=channel.visibility,
        created_by=channel.created_by,
        created_at=channel.created_at,
        unread_count=unread_count,
        mention_count=mention_count,
    )


def _message_response(
    message: ChatMessage,
    agent_state: str | None = None,
    session_id: str | None = None,
) -> MessageResponse:
    """Map a stored message onto its wire shape.

    :param agent_state: Optional thread ``agent_state`` for a thread-root
        message; ``None`` (the default) leaves the field unset so the
        wire shape omits it (replies and POST-created messages).
    :param session_id: Optional agent ``session_id`` for a thread-root
        message; ``None`` (the default) leaves the field unset, same rule.
    """
    # Pass the optional fields only when they are set so the wire shape
    # omits the keys on replies and on messages created by POST
    # (the routes use ``response_model_exclude_unset``). Pydantic adds an
    # assigned field to ``model_fields_set``, so assignment marks it set.
    response = MessageResponse(
        id=message.id,
        channel_id=message.channel_id,
        thread_root_id=message.thread_root_id,
        author_id=message.author_id,
        author_kind=message.author_kind,
        created_at=message.created_at,
        body=message.body,
        client_id=message.client_id,
    )
    if agent_state is not None:
        response.agent_state = agent_state
    if session_id is not None:
        response.session_id = session_id
    return response


def _agent_thread_response(thread: ChannelAgentThread) -> ChannelAgentThreadResponse:
    """Map a stored agent thread onto its wire shape.

    Optional fields are assigned only when set, so ``response_model_exclude_unset``
    omits a missing session or root body.
    """
    response = ChannelAgentThreadResponse(
        root_message_id=thread.root_message_id,
        agent_state=thread.agent_state,
    )
    if thread.session_id:
        response.session_id = thread.session_id
    if thread.body is not None:
        response.body = thread.body
    return response


def _cursor_string(cursor: tuple[int, str] | None) -> str | None:
    """Render a ``(created_at, id)`` cursor, or ``None``."""
    if cursor is None:
        return None
    return f"{cursor[0]}:{cursor[1]}"


def _validate_body(body: str) -> str:
    """Trim and validate a message body, returning the stored text.

    :raises OmnigentError: 422 ``empty_body``, ``body_too_long``, or
        ``nul_in_body``; nothing is stored on any of these.
    """
    trimmed = body.strip()
    if not trimmed:
        raise OmnigentError("body is empty", code=ErrorCode.EMPTY_BODY)
    if len(trimmed) > _MAX_BODY_CHARS:
        raise OmnigentError(
            f"body exceeds {_MAX_BODY_CHARS} characters",
            code=ErrorCode.BODY_TOO_LONG,
        )
    if "\u0000" in trimmed:
        raise OmnigentError("body contains U+0000", code=ErrorCode.NUL_IN_BODY)
    return trimmed


def _require_human(caller: str) -> None:
    """Refuse agent accounts on routes that mint or rotate credentials."""
    if principal_kind(caller) == "agent_account":
        raise OmnigentError(
            "agent accounts cannot manage agent accounts",
            code=ErrorCode.FORBIDDEN,
        )


def _audit_bot(
    request: Request,
    caller: str,
    chat_store: ChatStore,
    *,
    action: str,
    outcome: str,
    **extra: object,
) -> None:
    """Record a bot chat action with account and owner, never the body."""
    if principal_kind(caller) != "agent_account":
        return
    account = chat_store.get_agent_account_by_principal(caller)
    owner_id = account.owner_id if account is not None else request.scope.get("mcp_owner_id")
    account_id = account.id if account is not None else caller.removeprefix("agent:")
    # Attribute access so a request-scoped spy sees message ids. The name
    # imported above is bound at import and would skip that spy.
    import omnigent.debug_logging as debug_logging

    debug_logging.add_audit_attrs(
        actor_kind="agent_account",
        action=action,
        account_id=account_id,
        owner_id=owner_id if isinstance(owner_id, str) else None,
        outcome=outcome,
        **extra,
    )


def _raise_rate_limited(retry_after: int) -> None:
    """Raise the stable ``rate_limited`` error with a retry hint."""
    raise OmnigentError(
        "rate limit exceeded",
        code=ErrorCode.RATE_LIMITED,
        retry_after_seconds=retry_after,
    )


def _validate_display_name(value: str) -> str:
    """Trim a display name to the 1–40 character rule (FR-061)."""
    trimmed = value.strip()
    if not trimmed or len(trimmed) > 40 or "\u0000" in trimmed:
        raise OmnigentError("display_name must be 1-40 characters", code=ErrorCode.INVALID_INPUT)
    return trimmed


def _validate_handle(value: str) -> str:
    """Require a lowercase handle of 2–40 characters (FR-061)."""
    if not _HANDLE_RE.fullmatch(value):
        raise OmnigentError(
            "handle must match ^[a-z0-9][a-z0-9-]{1,39}$",
            code=ErrorCode.INVALID_INPUT,
        )
    return value


async def _channels_visible_to(
    chat_store: ChatStore, caller: str
) -> tuple[list[ChatChannel], list[ChatChannel]]:
    """Joined channels and DMs the caller may actually see.

    Agent accounts drop any conversation their owner is not a member of.
    """
    channels = list(await asyncio.to_thread(chat_store.list_joined_channels, caller))
    dms = list(await asyncio.to_thread(chat_store.list_joined_dms, caller))
    if principal_kind(caller) != "agent_account":
        return channels, dms
    account = await asyncio.to_thread(chat_store.get_agent_account_by_principal, caller)
    if account is None:
        return [], []
    owner_channels = {
        channel.id
        for channel in await asyncio.to_thread(chat_store.list_joined_channels, account.owner_id)
    }
    owner_dms = {
        channel.id
        for channel in await asyncio.to_thread(chat_store.list_joined_dms, account.owner_id)
    }
    allowed = owner_channels | owner_dms
    return (
        [channel for channel in channels if channel.id in allowed],
        [channel for channel in dms if channel.id in allowed],
    )


def _agent_account_response(account: ChatAgentAccount) -> AgentAccountResponse:
    """Wire shape of an agent account. The raw token is never included."""
    return AgentAccountResponse(
        id=account.id,
        principal_id=account.principal_id,
        owner_id=account.owner_id,
        display_name=account.display_name,
        handle=account.handle,
        token_prefix=account.token_prefix,
        secret_version=account.secret_version,
        last_seen_at=account.last_seen_at,
    )


def _require_agent_secret() -> None:
    """Refuse account minting when the HMAC secret is unset (FR-061)."""
    if agent_account_secret() is None:
        raise OmnigentError(
            "agent accounts are not configured",
            code=ErrorCode.AGENT_ACCOUNTS_UNCONFIGURED,
        )


def _audit_account(action: str, *, account_id: str | None, owner_id: str, outcome: str) -> None:
    """Record an account change without the token, secret, or webhook URL."""
    add_audit_attrs(
        action=action,
        account_id=account_id,
        owner_id=owner_id,
        outcome=outcome,
    )


def create_chat_router(
    chat_store: ChatStore,
    *,
    auth_provider: AuthProvider | None = None,
    feature_flags: FeatureFlags | None = None,
) -> APIRouter:
    """Build the ``/v1/chat`` router.

    :param chat_store: Team-chat persistence.
    :param auth_provider: Identifies the caller. ``None`` in single-user mode.
    :param feature_flags: Immutable release-feature snapshot; omitted
        resolves ``OMNIGENT_FEATURES`` at router construction.
    """
    from omnigent.server.chat_agent_threads import _bind_store

    # Idle after a restart can arrive before any chat request. Bind now so
    # that status can still copy the assistant text into the thread.
    _bind_store(chat_store)
    flags = feature_flags or resolve_feature_flags()

    async def _require_team_chat(request: Request) -> None:
        """Refuse every chat route with 404 when the flag is off (FR-003).

        Agent accounts also share the 120-request window with ``/mcp`` unless
        this call was started in-process by a tool (FR-092). Presence is
        stamped at most once per minute (FR-076).
        """
        if not flags.enabled(Feature.TEAM_CHAT):
            raise OmnigentError("team chat is disabled", code=ErrorCode.NOT_FOUND)
        if request.scope.get("mcp_inprocess"):
            return
        caller = _resolve_caller(request, auth_provider)
        if principal_kind(caller) != "agent_account":
            return
        retry_after = consume_agent_request(caller)
        if retry_after is not None:
            _raise_rate_limited(retry_after)
        await asyncio.to_thread(chat_store.touch_agent_last_seen, caller)

    async def _channels_with_counts(caller: str, rows: list[ChatChannel]) -> list[ChannelResponse]:
        """Attach unread and mention counts for *rows* (FR-008)."""
        counts = await asyncio.to_thread(
            chat_store.channel_unread_counts, caller, [row.id for row in rows]
        )
        rendered: list[ChannelResponse] = []
        for row in rows:
            unread, mentions = counts.get(row.id, (0, 0))
            rendered.append(_channel_response(row, unread_count=unread, mention_count=mentions))
        return rendered

    # The gate lives on the router, not on each handler: FastAPI validates
    # request bodies/query params before a handler runs, so a per-handler
    # check would let a malformed body surface as 422 even with the flag off.
    # A router dependency runs first, so every ``/v1/chat/*`` request answers
    # 404 regardless of its payload (FR-003).
    router = APIRouter(dependencies=[Depends(_require_team_chat)])

    @router.get("/chat/bootstrap", response_model=BootstrapResponse)
    async def bootstrap(request: Request) -> BootstrapResponse:
        """Ensure ``general`` exists, add the human caller, and return the sidebar.

        :raises OmnigentError: 404 when the flag is off.
        """
        caller = _resolve_caller(request, auth_provider)
        human_caller = caller if principal_kind(caller) == "user" else None
        general = await asyncio.to_thread(
            chat_store.bootstrap_channel, _GENERAL_CHANNEL, human_caller
        )
        channels, dms = await _channels_visible_to(chat_store, caller)
        members = await asyncio.to_thread(chat_store.list_members, general.id)
        accounts = await asyncio.to_thread(chat_store.list_active_agent_accounts)

        directory: list[DirectoryEntry] = [
            DirectoryEntry(id=member.principal_id, kind="user")
            for member in members
            if principal_kind(member.principal_id) == "user"
        ]
        directory.extend(
            DirectoryEntry(
                id=account.principal_id,
                kind="agent_account",
                display_name=f"{account.display_name} ({_human_label(account.owner_id)})",
                handle=account.handle,
                owner_id=account.owner_id,
            )
            for account in accounts
        )
        return BootstrapResponse(
            me=caller,
            channels=await _channels_with_counts(caller, channels),
            dms=await _channels_with_counts(caller, dms),
            directory=directory,
            changes_cursor=f"{now_epoch_us()}:{'0' * 32}",
        )

    @router.get("/chat/channels", response_model=list[ChannelResponse])
    async def list_channels(request: Request) -> list[ChannelResponse]:
        """Joined channels the caller may see, with unread counts (FR-008, FR-066)."""
        caller = _resolve_caller(request, auth_provider)
        channels, _dms = await _channels_visible_to(chat_store, caller)
        return await _channels_with_counts(caller, channels)

    @router.post("/chat/channels", response_model=ChannelResponse, status_code=201)
    async def create_channel(
        request: Request,
        body: ChannelCreateBody,
    ) -> ChannelResponse:
        """Create a public channel and make the caller a member (FR-005).

        An agent account also adds its owner, and may create a channel only
        because its owner may.
        """
        caller = _resolve_caller(request, auth_provider)
        if not _CHANNEL_NAME_RE.fullmatch(body.name):
            raise OmnigentError(
                "channel name must match ^[a-z0-9][a-z0-9-]{0,39}$",
                code=ErrorCode.INVALID_CHANNEL_NAME,
            )
        existing = await asyncio.to_thread(chat_store.get_channel_by_name, body.name)
        if existing is not None:
            raise OmnigentError("channel name already exists", code=ErrorCode.CONFLICT)
        channel = await asyncio.to_thread(chat_store.bootstrap_channel, body.name, caller)
        if principal_kind(caller) == "agent_account":
            account = await asyncio.to_thread(chat_store.get_agent_account_by_principal, caller)
            if account is not None and account.owner_id != caller:
                await asyncio.to_thread(chat_store.add_member, channel.id, account.owner_id)
        _audit_bot(
            request,
            caller,
            chat_store,
            action="chat.channel_create",
            outcome="ok",
            channel_id=channel.id,
        )
        return _channel_response(channel)

    @router.post("/chat/channels/{channel_id}/join", status_code=204)
    async def join_channel(request: Request, channel_id: str) -> None:
        """Add the caller to a public channel (FR-006)."""
        caller = _resolve_caller(request, auth_provider)
        channel = await asyncio.to_thread(chat_store.get_channel, channel_id)
        if channel is None or channel.kind != "channel":
            raise OmnigentError("Channel not found", code=ErrorCode.NOT_FOUND)
        await asyncio.to_thread(require_owner_membership, chat_store, channel, caller)
        await asyncio.to_thread(chat_store.add_member, channel.id, caller)

    @router.post("/chat/channels/{channel_id}/leave", status_code=204)
    async def leave_channel(request: Request, channel_id: str) -> None:
        """Remove the caller from a public channel; leaving ``general`` is 409."""
        caller = _resolve_caller(request, auth_provider)
        channel = await asyncio.to_thread(chat_store.get_channel, channel_id)
        if channel is None or channel.kind != "channel":
            raise OmnigentError("Channel not found", code=ErrorCode.NOT_FOUND)
        if channel.name == _GENERAL_CHANNEL:
            raise OmnigentError("cannot leave general", code=ErrorCode.CONFLICT)
        await asyncio.to_thread(chat_store.remove_member, channel.id, caller)

    @router.get(
        "/chat/channels/{channel_id}/messages",
        response_model=MessagePageResponse,
        # ``agent_state`` / ``session_id`` are unset on replies / thread-less
        # roots and must be omitted from the wire, not emitted as ``null``.
        response_model_exclude_unset=True,
    )
    async def list_messages(
        request: Request,
        channel_id: str,
        tail: bool | None = None,
        before: str | None = None,
        after: str | None = None,
        limit: int = _DEFAULT_MESSAGES_LIMIT,
        thread_root_id: str | None = None,
    ) -> MessagePageResponse:
        """Return one ascending page of messages (FR-019).

        ``tail`` selects the newest ``limit`` messages; otherwise ``before``
        or ``after`` (mutually exclusive) select a cursor-bound page.
        """
        caller = _resolve_caller(request, auth_provider)
        channel = await asyncio.to_thread(chat_store.get_channel, channel_id)
        if channel is None:
            raise OmnigentError("Channel not found", code=ErrorCode.NOT_FOUND)
        await asyncio.to_thread(require_read, chat_store, channel, caller)

        if before is not None and after is not None:
            raise OmnigentError(
                "before and after are mutually exclusive",
                code=ErrorCode.INVALID_PAGINATION,
            )
        if limit < 1 or limit > _MAX_MESSAGES_LIMIT:
            raise OmnigentError(
                f"limit must be between 1 and {_MAX_MESSAGES_LIMIT}",
                code=ErrorCode.INVALID_PAGINATION,
            )
        if tail and (before is not None or after is not None):
            raise OmnigentError(
                "tail cannot be combined with before or after",
                code=ErrorCode.INVALID_PAGINATION,
            )

        after_cursor = _parse_cursor(after)
        before_cursor = _parse_cursor(before)
        if thread_root_id is not None:
            from omnigent.server.chat_agent_threads import reconcile_thread_read

            await reconcile_thread_read(chat_store, thread_root_id)
        page = await asyncio.to_thread(
            chat_store.list_messages,
            channel.id,
            thread_root_id=thread_root_id,
            limit=limit,
            tail=limit if tail else None,
            after=after_cursor,
            before=before_cursor,
        )
        # Annotate thread-root messages with their agent thread state.
        # Replies (thread_root_id set) stay without it. One batched read
        # for the page, not one query per root.
        root_ids = [item.id for item in page.items if item.thread_root_id is None]
        agent_states = (
            await asyncio.to_thread(chat_store.list_thread_agent_states, root_ids)
            if root_ids
            else {}
        )
        session_ids = (
            await asyncio.to_thread(chat_store.list_thread_session_ids, root_ids)
            if root_ids
            else {}
        )
        items = [
            _message_response(
                item,
                agent_state=(agent_states.get(item.id) if item.thread_root_id is None else None),
                session_id=(session_ids.get(item.id) if item.thread_root_id is None else None),
            )
            for item in page.items
        ]
        return MessagePageResponse(
            items=items,
            has_older=page.has_older,
            has_more=page.has_more,
            next_after=_cursor_string(page.next_after),
            next_before=_cursor_string(page.next_before),
        )

    @router.get(
        "/chat/channels/{channel_id}/agent-threads",
        response_model=list[ChannelAgentThreadResponse],
        response_model_exclude_unset=True,
    )
    async def list_channel_agent_threads(
        request: Request, channel_id: str
    ) -> list[ChannelAgentThreadResponse]:
        """List active agent threads, including roots outside the message tail.

        Auth is ``require_read``, the same gate as ``list_messages``.
        Waiting is not stored on the thread; the client watches the
        returned sessions and keeps a strip row only while an approval
        is open.
        """
        caller = _resolve_caller(request, auth_provider)
        channel = await asyncio.to_thread(chat_store.get_channel, channel_id)
        if channel is None:
            raise OmnigentError("Channel not found", code=ErrorCode.NOT_FOUND)
        await asyncio.to_thread(require_read, chat_store, channel, caller)
        threads = await asyncio.to_thread(chat_store.list_channel_agent_threads, channel.id)
        return [_agent_thread_response(thread) for thread in threads]

    @router.post(
        "/chat/channels/{channel_id}/messages",
        response_model=MessageResponse,
        # Keep write responses stable: ``agent_state`` is never set here.
        response_model_exclude_unset=True,
    )
    async def post_message(
        request: Request,
        channel_id: str,
        body: MessagePostBody,
        response: Response,
    ) -> MessageResponse:
        """Store one message and announce it to members (FR-013..FR-016)."""
        caller = _resolve_caller(request, auth_provider)
        channel = await asyncio.to_thread(chat_store.get_channel, channel_id)
        if channel is None:
            raise OmnigentError("Channel not found", code=ErrorCode.NOT_FOUND)
        await asyncio.to_thread(require_post, chat_store, channel, caller)

        trimmed = _validate_body(body.body)
        if body.client_id is not None and not _CLIENT_ID_RE.fullmatch(body.client_id):
            raise OmnigentError(
                "client_id must match ^[A-Za-z0-9_-]{1,64}$",
                code=ErrorCode.INVALID_CLIENT_ID,
            )
        retry_after = consume_channel_post(caller, channel.id)
        if retry_after is not None:
            _raise_rate_limited(retry_after)
        if body.thread_root_id is not None and is_dismiss_command(trimmed):
            # Refused before storing, so a READ member's /dismiss leaves no row.
            await dismiss_agent_thread(
                chat_store, body.thread_root_id, caller, channel_id=channel.id
            )

        result = await asyncio.to_thread(
            chat_store.post_message,
            channel.id,
            caller,
            trimmed,
            author_kind=principal_kind(caller),
            client_id=body.client_id,
            thread_root_id=body.thread_root_id,
        )
        if result.status is PostStatus.CREATED and result.message is not None:
            response.status_code = 201
            await asyncio.to_thread(publish_message_created, chat_store, channel, result.message)
            from omnigent.server.chat_agent_threads import schedule_after_human_post

            await schedule_after_human_post(request, chat_store, channel, result.message)
            _audit_bot(
                request,
                caller,
                chat_store,
                action="chat.post",
                outcome="ok",
                message_id=result.message.id,
                channel_id=channel.id,
            )
            return _message_response(result.message)
        if result.status is PostStatus.REPLAYED and result.message is not None:
            response.status_code = 200
            return _message_response(result.message)
        if result.status is PostStatus.BODY_CONFLICT:
            raise OmnigentError(
                "client_id already used with a different body",
                code=ErrorCode.CLIENT_ID_CONFLICT,
            )
        if result.status is PostStatus.NESTED_THREAD:
            raise OmnigentError("thread_root_id names a reply", code=ErrorCode.NESTED_THREAD)
        if result.status is PostStatus.THREAD_ROOT_NOT_FOUND:
            raise OmnigentError(
                "thread_root_id does not name a message in this channel",
                code=ErrorCode.THREAD_ROOT_NOT_FOUND,
            )
        if result.status is PostStatus.BOT_STREAK_LIMIT:
            if result.notice is not None:
                await asyncio.to_thread(
                    publish_message_created, chat_store, channel, result.notice
                )
            raise OmnigentError(
                "bot streak limit reached; a human message is required",
                code=ErrorCode.BOT_STREAK_LIMIT,
            )
        raise OmnigentError(
            "message could not be stored",
            code=ErrorCode.INTERNAL_ERROR,
        )

    @router.post(
        "/chat/threads/{root_message_id}/dismiss",
        response_model=ChannelAgentThreadResponse,
        response_model_exclude_unset=True,
    )
    async def dismiss_thread_agent(
        request: Request, root_message_id: str
    ) -> ChannelAgentThreadResponse:
        """Stop forwarding thread replies to the agent; the session keeps running (FR-042)."""
        caller = _resolve_caller(request, auth_provider)
        thread = await asyncio.to_thread(chat_store.get_thread, root_message_id)
        if thread is None:
            raise OmnigentError("Thread not found", code=ErrorCode.NOT_FOUND)
        channel = await asyncio.to_thread(chat_store.get_channel, thread.channel_id)
        if channel is None:
            raise OmnigentError("Thread not found", code=ErrorCode.NOT_FOUND)
        await asyncio.to_thread(require_read, chat_store, channel, caller)
        link = await dismiss_agent_thread(chat_store, root_message_id, caller)
        if link is None:
            raise OmnigentError("thread has no agent to dismiss", code=ErrorCode.CONFLICT)
        _audit_bot(
            request,
            caller,
            chat_store,
            action="chat.thread.dismiss",
            outcome="ok",
            channel_id=channel.id,
        )
        response = ChannelAgentThreadResponse(
            root_message_id=root_message_id, agent_state=link.agent_state
        )
        if link.session_id:
            response.session_id = link.session_id
        return response

    @router.post("/chat/messages/{message_id}/forward", response_model=ForwardResponse)
    async def retry_message_forward(request: Request, message_id: str) -> ForwardResponse:
        """Retry a failed forward of one thread reply, at most once (FR-054)."""
        caller = _resolve_caller(request, auth_provider)
        state = await retry_forward(request, chat_store, message_id, caller)
        _audit_bot(
            request,
            caller,
            chat_store,
            action="chat.message.forward",
            outcome=state,
            message_id=message_id,
        )
        return ForwardResponse(id=message_id, forward_state=state)

    @router.post("/chat/dms", response_model=ChannelResponse)
    async def open_dm(
        request: Request,
        body: DmCreateBody,
        response: Response,
    ) -> ChannelResponse:
        """Return or create the DM for the caller plus 1–8 others (FR-021, FR-022)."""
        caller = _resolve_caller(request, auth_provider)
        if len(body.principals) < _MIN_DM_OTHERS or len(body.principals) > _MAX_DM_OTHERS:
            raise OmnigentError(
                "a DM takes between 1 and 8 other principals",
                code=ErrorCode.TOO_MANY_DM_PRINCIPALS,
            )
        result = await asyncio.to_thread(chat_store.get_or_create_dm, [caller, *body.principals])
        if result.status is DmStatus.CREATED and result.channel is not None:
            response.status_code = 201
            return _channel_response(result.channel)
        if result.status is DmStatus.EXISTING and result.channel is not None:
            response.status_code = 200
            return _channel_response(result.channel)
        if result.status is DmStatus.AGENT_OWNER_REQUIRED:
            raise OmnigentError(
                "an agent account requires its owner in the DM",
                code=ErrorCode.AGENT_OWNER_REQUIRED,
            )
        raise OmnigentError(
            "DM could not be opened",
            code=ErrorCode.INTERNAL_ERROR,
        )

    @router.get("/chat/channels/{channel_id}/members", response_model=list[MemberResponse])
    async def list_members(request: Request, channel_id: str) -> list[MemberResponse]:
        """List members and their principal ids."""
        caller = _resolve_caller(request, auth_provider)
        channel = await asyncio.to_thread(chat_store.get_channel, channel_id)
        if channel is None:
            raise OmnigentError("Channel not found", code=ErrorCode.NOT_FOUND)
        await asyncio.to_thread(require_read, chat_store, channel, caller)
        members = await asyncio.to_thread(chat_store.list_members, channel.id)
        accounts = {
            account.principal_id: account
            for account in await asyncio.to_thread(chat_store.list_active_agent_accounts)
        }
        now = now_epoch_us()
        rendered: list[MemberResponse] = []
        for member in members:
            kind = (
                "agent_account"
                if principal_kind(member.principal_id) == "agent_account"
                else "human"
            )
            account = accounts.get(member.principal_id)
            display = account.display_name if account is not None else member.principal_id
            status = None
            if kind == "agent_account":
                seen = account.last_seen_at if account is not None else None
                status = (
                    "aktiv" if seen is not None and now - seen <= _ACTIVE_WINDOW_US else "inaktiv"
                )
            rendered.append(
                MemberResponse(
                    principal_id=member.principal_id,
                    principal_type=kind,
                    role=member.role,
                    joined_at=member.joined_at,
                    kind=kind,
                    display=display,
                    status=status,
                )
            )
        return rendered

    @router.get("/chat/unread", response_model=UnreadResponse)
    async def unread(request: Request) -> UnreadResponse:
        """Return an agent account's unread mentions and DM messages (FR-088).

        Humans receive 403 and use bootstrap counts instead.
        """
        caller = _resolve_caller(request, auth_provider)
        if principal_kind(caller) != "agent_account":
            raise OmnigentError(
                "humans read unread counts from bootstrap",
                code=ErrorCode.FORBIDDEN,
            )
        items = await asyncio.to_thread(chat_store.list_unread, caller, limit=_UNREAD_LIMIT)
        accounts = {
            account.principal_id: account.display_name
            for account in await asyncio.to_thread(chat_store.list_active_agent_accounts)
        }
        rendered: list[UnreadItemResponse] = []
        for item in items:
            kind = author_principal_type(item.message.author_kind)
            display = accounts.get(item.message.author_id) or (
                "system" if kind == "system" else item.message.author_id
            )
            rendered.append(
                UnreadItemResponse(
                    channel_id=item.message.channel_id,
                    thread_root_id=item.message.thread_root_id,
                    message_id=item.message.id,
                    author=AuthorResponse(
                        id=item.message.author_id,
                        kind=kind,
                        display=display,
                    ),
                    body=item.message.body,
                    created_at=item.message.created_at,
                    reason=item.reason,
                )
            )
        return UnreadResponse(items=rendered, next_cursor=None)

    @router.post("/chat/channels/{channel_id}/read")
    async def mark_read(request: Request, channel_id: str, body: ReadBody) -> JSONResponse:
        """Advance the caller's read cursor. Older cursors stay put (FR-007).

        Unknown ids and a ``created_at`` that does not match the message are
        422 and leave the cursor unchanged. A forward move notifies only the
        caller (FR-009). The body is returned with status 204 so MCP can read
        the cursor.
        """
        caller = _resolve_caller(request, auth_provider)
        message_id = body.id or body.message_id
        if not message_id or (body.id and body.message_id and body.id != body.message_id):
            raise OmnigentError("id is required", code=ErrorCode.INVALID_CURSOR)
        channel = await asyncio.to_thread(chat_store.get_channel, channel_id)
        if channel is None:
            raise OmnigentError("Channel not found", code=ErrorCode.NOT_FOUND)
        await asyncio.to_thread(require_read, chat_store, channel, caller)
        result = await asyncio.to_thread(
            chat_store.mark_read,
            channel.id,
            caller,
            message_id,
            thread_root_id=body.thread_root_id,
            created_at=body.created_at,
        )
        if result.status is ReadStatus.NOT_FOUND:
            raise OmnigentError("message not found", code=ErrorCode.INVALID_CURSOR)
        if result.status is ReadStatus.WRONG_SCOPE:
            raise OmnigentError(
                "message is not in the requested thread scope",
                code=ErrorCode.INVALID_CURSOR,
            )
        cursor = result.cursor
        if cursor is None or cursor.last_read_at is None or cursor.last_read_id is None:
            raise OmnigentError("read cursor was not stored", code=ErrorCode.INTERNAL_ERROR)
        if result.status is ReadStatus.ADVANCED:
            publish_read_updated(
                caller,
                channel_id=cursor.channel_id,
                thread_root_id=cursor.thread_root_id,
                created_at=cursor.last_read_at,
                message_id=cursor.last_read_id,
            )
        return JSONResponse(
            status_code=204,
            content={
                "channel_id": cursor.channel_id,
                "thread_root_id": cursor.thread_root_id,
                "cursor": {"created_at": cursor.last_read_at, "id": cursor.last_read_id},
                "advanced": result.status is ReadStatus.ADVANCED,
            },
        )

    @router.get("/chat/agent-accounts", response_model=list[AgentAccountResponse])
    async def list_agent_accounts(request: Request) -> list[AgentAccountResponse]:
        """List active agent accounts owned by the caller.

        Another human's accounts are not included. ``last_seen_at`` is
        epoch microseconds, or null when the account has never called.
        """
        caller = _resolve_caller(request, auth_provider)
        _require_human(caller)
        accounts = await asyncio.to_thread(chat_store.list_active_agent_accounts)
        owned = [account for account in accounts if account.owner_id == caller]
        return [_agent_account_response(account) for account in owned]

    @router.post(
        "/chat/agent-accounts", response_model=AgentAccountCreatedResponse, status_code=201
    )
    async def create_agent_account(
        request: Request, body: AgentAccountBody
    ) -> AgentAccountCreatedResponse:
        """Mint an agent account. The raw ``ogc_`` token is returned once."""
        caller = _resolve_caller(request, auth_provider)
        _require_human(caller)
        _require_agent_secret()
        display_name = _validate_display_name(body.display_name)
        handle = _validate_handle(body.handle)
        # The R1 client sends no webhook. Blank is stored; a URL that is
        # present still has to be public https.
        webhook = (body.webhook_url or "").strip()
        if webhook and await asyncio.to_thread(webhook_url_rejected, webhook):
            raise OmnigentError("webhook_url is invalid", code=ErrorCode.INVALID_WEBHOOK)
        result = await asyncio.to_thread(
            chat_store.create_agent_account,
            caller,
            display_name,
            handle,
            webhook_url=webhook,
        )
        if result.status is AccountStatus.HANDLE_TAKEN:
            raise OmnigentError("handle already exists", code=ErrorCode.CONFLICT)
        if result.status is AccountStatus.LIMIT:
            _audit_account(
                "chat.agent_account.create",
                account_id=None,
                owner_id=caller,
                outcome="agent_account_limit",
            )
            raise OmnigentError(
                "agent account limit reached",
                code=ErrorCode.AGENT_ACCOUNT_LIMIT,
            )
        if result.issue is None:
            raise OmnigentError("agent account was not created", code=ErrorCode.INTERNAL_ERROR)
        account = result.issue.account
        _audit_account(
            "chat.agent_account.create",
            account_id=account.id,
            owner_id=caller,
            outcome="created",
        )
        return AgentAccountCreatedResponse(
            account=_agent_account_response(account),
            token=result.issue.token,
            webhook_secret=derive_webhook_secret(account.id, account.secret_version),
        )

    @router.post(
        "/chat/agent-accounts/{account_id}/rotate",
        response_model=AgentAccountRotatedResponse,
    )
    async def rotate_agent_account(
        request: Request, account_id: str
    ) -> AgentAccountRotatedResponse:
        """Rotate the token. The previous ``ogc_`` value returns 401."""
        caller = _resolve_caller(request, auth_provider)
        _require_human(caller)
        _require_agent_secret()
        result = await asyncio.to_thread(chat_store.rotate_agent_token, account_id, caller)
        if result.status is AccountStatus.NOT_FOUND:
            raise OmnigentError("agent account not found", code=ErrorCode.NOT_FOUND)
        if result.status is AccountStatus.REVOKED_ACCOUNT:
            raise OmnigentError("agent account is revoked", code=ErrorCode.CONFLICT)
        if result.issue is None:
            raise OmnigentError("agent token was not issued", code=ErrorCode.INTERNAL_ERROR)
        account = result.issue.account
        _audit_account(
            "chat.agent_account.rotate",
            account_id=account.id,
            owner_id=caller,
            outcome="rotated",
        )
        return AgentAccountRotatedResponse(
            account=_agent_account_response(account),
            token=result.issue.token,
            webhook_secret=derive_webhook_secret(account.id, account.secret_version),
        )

    async def _revoke(caller: str, account_id: str) -> None:
        result = await asyncio.to_thread(chat_store.revoke_agent_account, account_id, caller)
        if result.status is AccountStatus.NOT_FOUND:
            raise OmnigentError("agent account not found", code=ErrorCode.NOT_FOUND)
        if result.status is not AccountStatus.REVOKED:
            raise OmnigentError("agent account was not revoked", code=ErrorCode.INTERNAL_ERROR)
        _audit_account(
            "chat.agent_account.revoke",
            account_id=account_id,
            owner_id=caller,
            outcome="revoked",
        )

    @router.post("/chat/agent-accounts/{account_id}/revoke", status_code=204)
    async def revoke_agent_account(request: Request, account_id: str) -> None:
        """Revoke the account. Its current token returns 401."""
        caller = _resolve_caller(request, auth_provider)
        _require_human(caller)
        await _revoke(caller, account_id)

    @router.delete("/chat/agent-accounts/{account_id}", status_code=204)
    async def delete_agent_account(request: Request, account_id: str) -> None:
        """Revoke the account (FR-064). A non-owner receives 404."""
        caller = _resolve_caller(request, auth_provider)
        _require_human(caller)
        await _revoke(caller, account_id)

    @router.get("/chat/changes", response_model=ChangesResponse)
    async def changes(
        request: Request,
        after: str | None = None,
    ) -> ChangesResponse:
        """Return message ids created after ``after`` for backfill."""
        caller = _resolve_caller(request, auth_provider)
        after_cursor = _parse_cursor(after)
        page = await asyncio.to_thread(
            chat_store.list_changes, caller, after=after_cursor, limit=_CHANGES_LIMIT
        )
        return ChangesResponse(
            items=[
                ChangeItem(
                    kind="message",
                    id=item.id,
                    channel_id=item.channel_id,
                    thread_root_id=item.thread_root_id,
                    created_at=item.created_at,
                )
                for item in page.items
            ],
            next=_cursor_string(page.next_after),
            reset=page.reset,
        )

    return router
