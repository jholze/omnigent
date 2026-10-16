"""Delivery-status route for the manager webhook outbox."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query, Request

from omnigent.entities import LifecycleOutboxEvent
from omnigent.server.auth import LEVEL_MANAGE, AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id as _get_user_id
from omnigent.server.routes._auth_helpers import (
    require_access_and_level as _require_access_and_level,
)
from omnigent.server.routes._errors import session_not_found as _session_not_found
from omnigent.stores import ConversationStore
from omnigent.stores.permission_store import PermissionStore
from omnigent.stores.session_lifecycle_store import SessionLifecycleStore


def _delivery_to_response(event: LifecycleOutboxEvent) -> dict[str, Any]:
    """Delivery status for one outbox row. The payload body is not included."""
    return {
        "event_id": event.id,
        "event_type": event.event_type,
        "sequence": event.sequence,
        "status": event.status,
        "attempt_count": event.attempt_count,
        "last_attempt_at": event.last_attempt_at,
        "delivered_at": event.delivered_at,
        "last_http_status": event.last_http_status,
        "last_error_code": event.last_error_code,
        "last_error_message": event.last_error_message,
    }


def _get_session_lifecycle_store(request: Request) -> SessionLifecycleStore | None:
    return getattr(request.app.state, "session_lifecycle_store", None)


def register_manager_webhook_routes(
    router: APIRouter,
    *,
    conversation_store: ConversationStore,
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
) -> None:
    """Register ``GET /sessions/{id}/manager-webhook-deliveries``."""

    @router.get(
        "/sessions/{session_id}/manager-webhook-deliveries",
        include_in_schema=False,
        response_model=None,
    )
    async def list_manager_webhook_deliveries(
        request: Request,
        session_id: str,
        limit: int = Query(default=100, ge=1, le=1000),
        after: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """List a session's manager-webhook outbox rows, newest first.

        Requires manage (or owner) access. ``last_error_message`` can
        describe the receiver, so a public-link viewer is not enough.

        :param after: Opaque cursor (an event id). Returns older rows.
        :returns: ``{"deliveries": [...], "next_cursor": ...}``.
        """
        user_id = _get_user_id(request, auth_provider)
        access = await _require_access_and_level(
            user_id, session_id, LEVEL_MANAGE, permission_store, conversation_store
        )
        conv = access.conversation
        if conv is None:
            conv = await asyncio.to_thread(conversation_store.get_conversation, session_id)
            if conv is None:
                raise _session_not_found()
        store = _get_session_lifecycle_store(request)
        if store is None:
            return {"deliveries": [], "next_cursor": None}
        deliveries, next_cursor = await asyncio.to_thread(
            store.list_deliveries, session_id, limit=limit, after_id=after
        )
        return {
            "deliveries": [_delivery_to_response(row) for row in deliveries],
            "next_cursor": next_cursor,
        }
