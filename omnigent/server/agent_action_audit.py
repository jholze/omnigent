"""Audit attributes for an agent-account action.

The request middleware calls :func:`record_agent_action` after the route
returns, so a chat or in-process ``/v1`` handler cannot forget the bot and
its owner. Arguments and message bodies are never copied.
"""

from __future__ import annotations

from typing import Any

import omnigent.debug_logging as debug_logging


def record_agent_action(request: Any, status_code: int | None) -> None:
    """Attach bot identity to the current request's audit bag.

    External ``/mcp`` calls are recorded by the tool dispatcher. This covers
    ``/v1/chat/*`` and in-process ``/v1`` calls made for an agent account.
    """
    scope = getattr(request, "scope", {})
    actor = scope.get("mcp_actor")
    if not isinstance(actor, str) or not actor.startswith("agent:"):
        return
    path = request.url.path
    inprocess = scope.get("mcp_inprocess") is True
    if path == "/mcp" or path.startswith("/mcp/"):
        return
    if not inprocess and not path.startswith("/v1/chat"):
        return
    account_id = scope.get("mcp_account_id")
    owner_id = scope.get("mcp_owner_id")
    outcome = "ok" if status_code is not None and status_code < 400 else "error"
    debug_logging.add_audit_attrs(
        actor_kind="agent_account",
        account_id=account_id if isinstance(account_id, str) else None,
        owner_id=owner_id if isinstance(owner_id, str) else None,
        action=_action_name(request.method, path),
        outcome=outcome,
    )


def _action_name(method: str, path: str) -> str:
    """Stable action name. Target ids stay in the route's own attributes."""
    trimmed = path.rstrip("/") or "/"
    if method == "POST" and trimmed.endswith("/messages"):
        return "chat.post"
    if method == "POST" and trimmed == "/v1/sessions":
        return "sessions.create"
    if method == "POST" and trimmed == "/v1/chat/channels":
        return "chat.channel_create"
    return f"{method} {trimmed}"
