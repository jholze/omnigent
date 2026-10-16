"""Human chat beside a shared agent session.

``GET`` and ``POST /v1/sessions/{id}/chat``. The id may be the root or a
child. Lines are stored on the root. A post does not create a conversation
item and does not enter the agent prompt.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from pydantic import BaseModel, field_validator

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import LEVEL_READ, AuthProvider
from omnigent.server.permissions import check_session_access
from omnigent.server.routes._auth_helpers import attribution_user, get_user_id
from omnigent.server.routes._errors import session_not_found
from omnigent.server.session_chat import (
    human_chat_is_shared,
    may_read_human_chat,
    publish_human_chat,
)
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.permission_store import PermissionStore
from omnigent.stores.session_chat_store import SessionChatMessage, SessionChatStore

_MAX_BODY_CHARS = 2000


class SessionChatPostBody(BaseModel):
    """Request body for ``POST /v1/sessions/{id}/chat``.

    :param body: The line. Trimmed, required, at most 2000 characters.
    """

    body: str

    @field_validator("body")
    @classmethod
    def _trimmed_body(cls, value: str) -> str:
        """Reject empty and over-long lines before anything is stored."""
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("body is empty")
        if len(trimmed) > _MAX_BODY_CHARS:
            raise ValueError("body exceeds 2000 characters")
        return trimmed


class SessionChatMessageResponse(BaseModel):
    """One stored line.

    :param id: Line id.
    :param created_by: Author, or ``None`` in single-user mode.
    :param created_at: Unix epoch microseconds.
    :param body: The text.
    """

    id: str
    created_by: str | None
    created_at: int
    body: str


class SessionChatListResponse(BaseModel):
    """``GET /v1/sessions/{id}/chat``.

    :param items: Lines ordered by ``(created_at, id)`` ascending.
    :param shared: True when another person has a direct grant on the root.
    """

    items: list[SessionChatMessageResponse]
    shared: bool


def _message_response(message: SessionChatMessage) -> SessionChatMessageResponse:
    """Map a stored line onto the wire shape."""
    return SessionChatMessageResponse(
        id=message.id,
        created_by=message.created_by,
        created_at=message.created_at,
        body=message.body,
    )


def _parse_after(after: str | None) -> tuple[int, str] | None:
    """Parse an exclusive ``created_at:id`` cursor.

    :param after: Query value, or ``None`` when the client wants every line.
    :returns: The pair, or ``None``.
    :raises OmnigentError: 422 when the cursor is not that pair.
    """
    if after is None or after == "":
        return None
    created_raw, separator, message_id = after.partition(":")
    if separator != ":" or not message_id or not created_raw.isdigit():
        raise OmnigentError(
            "after must be created_at:id",
            code=ErrorCode.INVALID_INPUT,
        )
    return int(created_raw), message_id


def create_session_chat_router(
    store: SessionChatStore,
    conversation_store: ConversationStore,
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
) -> APIRouter:
    """Build ``/sessions/{session_id}/chat``.

    :param store: Persistence for the lines.
    :param conversation_store: Resolves a child id to its root.
    :param auth_provider: Identifies the caller. ``None`` in single-user mode.
    :param permission_store: Grant store. ``None`` allows every caller.
    :returns: The router, mounted by ``create_app`` at ``/v1``.
    """
    router = APIRouter()

    async def _root_for_chat(request: Request, session_id: str) -> tuple[str, str | None]:
        """Resolve ``session_id`` to the root and enforce the chat rule.

        No access at all is 404. A caller who can see the session but only
        through ``__public__`` is 403.

        :param request: Incoming request.
        :param session_id: Root or child conversation id.
        :returns: ``(root_conversation_id, user_id)``.
        :raises OmnigentError: 401, 403, or 404.
        """
        user_id = get_user_id(request, auth_provider)
        if permission_store is not None and user_id is None:
            raise OmnigentError(
                "Authentication required",
                code=ErrorCode.UNAUTHORIZED,
            )
        conversation = await asyncio.to_thread(conversation_store.get_conversation, session_id)
        if conversation is None:
            raise session_not_found()
        root_id = conversation.root_conversation_id
        if permission_store is None:
            return root_id, user_id
        allowed = await asyncio.to_thread(
            may_read_human_chat, user_id, root_id, permission_store
        )
        if allowed:
            return root_id, user_id
        can_see_session = await asyncio.to_thread(
            check_session_access,
            user_id,
            session_id,
            LEVEL_READ,
            permission_store,
            conversation_store,
        )
        if can_see_session:
            raise OmnigentError(
                "Human chat is not available on this session",
                code=ErrorCode.FORBIDDEN,
            )
        raise session_not_found()

    @router.get("/sessions/{session_id}/chat", response_model=SessionChatListResponse)
    async def list_session_chat(
        request: Request,
        session_id: str,
        after: str | None = None,
    ) -> SessionChatListResponse:
        """Return the stored lines and whether the session is shared.

        :param request: Incoming request.
        :param session_id: Root or child conversation id.
        :param after: Exclusive ``created_at:id`` cursor.
        :returns: ``{ items, shared }``.
        """
        root_id, user_id = await _root_for_chat(request, session_id)
        cursor = _parse_after(after)
        items = await asyncio.to_thread(store.list_for_root, root_id, after=cursor)
        shared = await asyncio.to_thread(
            human_chat_is_shared, user_id, root_id, permission_store
        )
        return SessionChatListResponse(
            items=[_message_response(item) for item in items],
            shared=shared,
        )

    @router.post(
        "/sessions/{session_id}/chat",
        response_model=SessionChatMessageResponse,
        status_code=201,
    )
    async def post_session_chat(
        request: Request,
        session_id: str,
        payload: SessionChatPostBody,
    ) -> SessionChatMessageResponse:
        """Store one line and announce it. Does not talk to the agent.

        :param request: Incoming request.
        :param session_id: Root or child conversation id.
        :param payload: The trimmed line.
        :returns: The stored line.
        """
        root_id, user_id = await _root_for_chat(request, session_id)
        message = await asyncio.to_thread(
            store.append,
            root_id,
            payload.body,
            attribution_user(user_id),
        )
        await asyncio.to_thread(
            publish_human_chat,
            conversation_store,
            root_id,
            message.id,
            message.created_at,
        )
        return _message_response(message)

    return router
