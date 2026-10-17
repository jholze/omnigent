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
from pydantic import BaseModel

from omnigent.db.utils import now_epoch_us
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import RESERVED_USER_LOCAL, AuthProvider
from omnigent.server.chat_access import principal_kind, require_post, require_read
from omnigent.server.chat_realtime import publish_message_created
from omnigent.server.feature_flags import Feature, FeatureFlags, resolve_feature_flags
from omnigent.server.routes._auth_helpers import require_user
from omnigent.stores.chat_store import (
    ChatChannel,
    ChatMessage,
    ChatStore,
    DmStatus,
    PostStatus,
)

_GENERAL_CHANNEL = "general"
_MAX_BODY_CHARS = 4000
_MIN_DM_OTHERS = 1
_MAX_DM_OTHERS = 8
_DEFAULT_MESSAGES_LIMIT = 50
_MAX_MESSAGES_LIMIT = 200
_CHANGES_LIMIT = 1000
_CHANNEL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


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


class MessageResponse(BaseModel):
    """Wire shape of one stored message."""

    id: str
    channel_id: str
    thread_root_id: str | None
    author_id: str
    author_kind: str
    created_at: int
    body: str
    client_id: str | None


class DirectoryEntry(BaseModel):
    """One mentionable principal in the bootstrap directory."""

    id: str
    kind: str
    display_name: str | None = None
    handle: str | None = None


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


def _resolve_caller(request: Request, auth_provider: AuthProvider | None) -> str:
    """Resolve the caller principal, falling back to the local user.

    :raises OmnigentError: 401 when multi-user auth is active but the
        request carries no identity.
    """
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


def _channel_response(channel: ChatChannel) -> ChannelResponse:
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
    )


def _message_response(message: ChatMessage) -> MessageResponse:
    """Map a stored message onto its wire shape."""
    return MessageResponse(
        id=message.id,
        channel_id=message.channel_id,
        thread_root_id=message.thread_root_id,
        author_id=message.author_id,
        author_kind=message.author_kind,
        created_at=message.created_at,
        body=message.body,
        client_id=message.client_id,
    )


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
    flags = feature_flags or resolve_feature_flags()

    def _require_team_chat() -> None:
        """Refuse every chat route with 404 when the flag is off (FR-003)."""
        if not flags.enabled(Feature.TEAM_CHAT):
            raise OmnigentError("team chat is disabled", code=ErrorCode.NOT_FOUND)

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
        channels = await asyncio.to_thread(chat_store.list_joined_channels, caller)
        dms = await asyncio.to_thread(chat_store.list_joined_dms, caller)
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
                display_name=account.display_name,
                handle=account.handle,
            )
            for account in accounts
        )
        return BootstrapResponse(
            me=caller,
            channels=[_channel_response(channel) for channel in channels],
            dms=[_channel_response(dm) for dm in dms],
            directory=directory,
            changes_cursor=f"{now_epoch_us()}:{'0' * 32}",
        )

    @router.post("/chat/channels", response_model=ChannelResponse, status_code=201)
    async def create_channel(
        request: Request,
        body: ChannelCreateBody,
    ) -> ChannelResponse:
        """Create a public channel and make the caller a member (FR-005)."""
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
        return _channel_response(channel)

    @router.post("/chat/channels/{channel_id}/join", status_code=204)
    async def join_channel(request: Request, channel_id: str) -> None:
        """Add the caller to a public channel (FR-006)."""
        caller = _resolve_caller(request, auth_provider)
        channel = await asyncio.to_thread(chat_store.get_channel, channel_id)
        if channel is None or channel.kind != "channel":
            raise OmnigentError("Channel not found", code=ErrorCode.NOT_FOUND)
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

    @router.get("/chat/channels/{channel_id}/messages", response_model=MessagePageResponse)
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
        page = await asyncio.to_thread(
            chat_store.list_messages,
            channel.id,
            thread_root_id=thread_root_id,
            limit=limit,
            tail=limit if tail else None,
            after=after_cursor,
            before=before_cursor,
        )
        return MessagePageResponse(
            items=[_message_response(item) for item in page.items],
            has_older=page.has_older,
            has_more=page.has_more,
            next_after=_cursor_string(page.next_after),
            next_before=_cursor_string(page.next_before),
        )

    @router.post("/chat/channels/{channel_id}/messages", response_model=MessageResponse)
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
        raise OmnigentError(
            "message could not be stored",
            code=ErrorCode.INTERNAL_ERROR,
        )

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
