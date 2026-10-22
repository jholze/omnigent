"""Project allow-list for scoped agent accounts (ADR-0011).

An agent account whose ``project_ids`` is not NULL acts with its owner's
rights intersected with sessions whose root session is filed in one of those
projects. The allow-list is read from the account row on every request and
bound for that request only. Endpoint classes outside the scope answer 403
``out_of_scope``; out-of-scope sessions and projects answer 404.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from omnigent.errors import ErrorCode, OmnigentError

_logger = logging.getLogger(__name__)

# Bounds the parent walk for legacy rows without a root pointer.
_MAX_PARENT_DEPTH = 64


@dataclass(frozen=True)
class ProjectScope:
    """The allow-list of one scoped agent account for the current request.

    :param account_id: The agent account id, for audit.
    :param project_ids: Allowed project ids. Empty denies every session.
    :param route: Request path, for audit.
    :param conversation_store: Store used to resolve a session's root.
    """

    account_id: str
    project_ids: frozenset[str]
    route: str = ""
    conversation_store: Any | None = None


_scope_var: ContextVar[ProjectScope | None] = ContextVar("agent_project_scope", default=None)


def current_project_scope() -> ProjectScope | None:
    """Return the scope bound for this request, or ``None`` when unscoped."""
    return _scope_var.get()


@contextmanager
def bind_project_scope(scope: ProjectScope | None) -> Iterator[None]:
    """Bind *scope* for the duration of one request, then restore the outer value."""
    token = _scope_var.set(scope)
    try:
        yield
    finally:
        _scope_var.reset(token)


def record_scope_denial(reason: str, scope: ProjectScope | None = None) -> None:
    """Audit a scope denial with account id, route, and reason. Never a credential."""
    active = scope or current_project_scope()
    if active is None:
        return
    import omnigent.debug_logging as debug_logging

    debug_logging.add_audit_attrs(
        actor_kind="agent_account",
        account_id=active.account_id,
        scope_denied=reason,
        denied_route=active.route,
    )
    _logger.info(
        "Agent account scope denial account_id=%s route=%s reason=%s",
        active.account_id,
        active.route,
        reason,
    )


def out_of_scope(reason: str) -> OmnigentError:
    """Audit and build the 403 ``out_of_scope`` refusal."""
    record_scope_denial(reason)
    return OmnigentError("Not allowed for this agent account", code=ErrorCode.OUT_OF_SCOPE)


def _store_for(scope: ProjectScope, conversation_store: Any | None) -> Any | None:
    if conversation_store is not None:
        return conversation_store
    if scope.conversation_store is not None:
        return scope.conversation_store
    try:
        from omnigent.runtime import get_conversation_store

        return get_conversation_store()
    except RuntimeError:
        return None


def _root_conversation(store: Any, conversation: Any) -> Any | None:
    """Return the top-level session of *conversation*, or ``None`` when missing."""
    if conversation.parent_conversation_id is None:
        return conversation
    root_id = getattr(conversation, "root_conversation_id", None)
    if root_id and root_id != conversation.id:
        return store.get_conversation(root_id)
    current = conversation
    for _ in range(_MAX_PARENT_DEPTH):
        if current.parent_conversation_id is None:
            return current
        current = store.get_conversation(current.parent_conversation_id)
        if current is None:
            return None
    return None


def session_in_scope(
    scope: ProjectScope,
    conversation_id: str,
    conversation_store: Any | None = None,
    conversation: Any | None = None,
) -> bool:
    """Whether the root of *conversation_id* is filed in an allowed project.

    Sub-agent sessions follow their root session. A missing session, a
    missing store, or an unfiled root is out of scope.
    """
    if not scope.project_ids:
        return False
    store = _store_for(scope, conversation_store)
    if store is None:
        return False
    conv = conversation
    if conv is None or conv.id != conversation_id:
        conv = store.get_conversation(conversation_id)
    if conv is None:
        return False
    root = _root_conversation(store, conv)
    return root is not None and root.project_id in scope.project_ids


def require_session_in_scope(conversation_id: str, conversation_store: Any | None = None) -> None:
    """Raise 404 when a scoped account reaches a session outside its projects."""
    scope = current_project_scope()
    if scope is None or session_in_scope(scope, conversation_id, conversation_store):
        return
    record_scope_denial("session_out_of_scope", scope)
    raise OmnigentError("Conversation not found", code=ErrorCode.NOT_FOUND)


def require_project_in_scope(project_id: str | None) -> None:
    """Refuse a session target project outside the allow-list.

    A missing or empty project is 403 ``out_of_scope``; another project is
    404 so its existence is not confirmed.
    """
    scope = current_project_scope()
    if scope is None:
        return
    if not project_id:
        raise out_of_scope("project_required")
    if project_id not in scope.project_ids:
        record_scope_denial("project_out_of_scope", scope)
        raise OmnigentError("Project not found", code=ErrorCode.NOT_FOUND)


def require_create_in_scope(
    project_id: str | None,
    parent_session_id: str | None,
    conversation_store: Any | None = None,
) -> None:
    """Refuse a session create outside the allow-list before any row is written.

    A sub-agent create follows its parent's root. Any other create needs an
    allowed ``project_id``.
    """
    scope = current_project_scope()
    if scope is None:
        return
    if parent_session_id:
        require_session_in_scope(parent_session_id, conversation_store)
        if project_id:
            require_project_in_scope(project_id)
        return
    require_project_in_scope(project_id)


def _session_subpath_denial(method: str, rest: str) -> str | None:
    """Classify ``/v1/sessions/{id}/<rest>``. Session access is checked by the route."""
    if rest.startswith("resources/environments/") and rest.endswith("/shell"):
        return "workspace_shell"
    if rest == "permissions" or rest.startswith("permissions/"):
        return None if method == "GET" else "sharing"
    return None


def denial_reason(method: str, path: str, query: dict[str, str]) -> str | None:
    """Return why a scoped account may not call this endpoint, or ``None``.

    Unlisted endpoints are refused so a new route does not widen the scope.
    """
    trimmed = path.rstrip("/") or "/"
    read = method in ("GET", "HEAD")
    if trimmed == "/mcp":
        return None
    if trimmed in ("/health", "/v1/info"):
        return None if read else "unscoped_endpoint"
    if trimmed == "/v1/chat/agent-accounts" or trimmed.startswith("/v1/chat/agent-accounts/"):
        return "agent_account_management"
    if trimmed == "/v1/chat" or trimmed.startswith("/v1/chat/"):
        return None
    if trimmed == "/v1/sessions":
        return None if method in ("GET", "HEAD", "POST") else "unscoped_endpoint"
    if trimmed == "/v1/sessions/projects":
        return None if read else "project_management"
    if trimmed.startswith("/v1/sessions/"):
        rest = trimmed.removeprefix("/v1/sessions/").partition("/")[2]
        return _session_subpath_denial(method, rest)
    if trimmed == "/v1/projects":
        return None if read else "project_management"
    if trimmed.startswith("/v1/projects/"):
        tail = trimmed.removeprefix("/v1/projects/")
        if read and tail != "order" and "/" not in tail:
            return None
        return "project_management"
    if trimmed.startswith("/v1/hosts/") and trimmed.endswith("/runners") and method == "POST":
        # Launching a runner names a session; its session check applies.
        return None
    if trimmed == "/v1/hosts" or trimmed.startswith("/v1/hosts/"):
        return "host_management"
    if trimmed == "/v1/skills":
        return None if read and query.get("session_id") else "host_management"
    for prefix in ("/v1/agents", "/v1/harnesses", "/v1/extensions"):
        if trimmed == prefix or trimmed.startswith(prefix + "/"):
            return None if read else "unscoped_endpoint"
    if trimmed == "/v1/sharing" or trimmed.startswith("/v1/sharing/"):
        return "sharing"
    for prefix in ("/v1/policies", "/v1/policy-registry"):
        if trimmed == prefix or trimmed.startswith(prefix + "/"):
            return "global_policy"
    if trimmed == "/v1/users" or trimmed.startswith("/v1/users/"):
        return "admin"
    return "unscoped_endpoint"


class AgentProjectScopeMiddleware:
    """Resolve a scoped agent account's allow-list for each request.

    The account is re-read through the same ``ogc_`` resolution as
    authentication, so a list change, revoke, or rotate applies to the next
    request. A request without an ``ogc_`` bearer runs with no scope bound.
    """

    def __init__(self, app: ASGIApp, conversation_store: Any | None = None) -> None:
        self._app = app
        self._conversation_store = conversation_store

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return
        project_scope = await self._resolve(scope)
        if project_scope is not None:
            connection = HTTPConnection(scope)
            method = scope.get("method", "GET") if scope["type"] == "http" else "GET"
            reason = denial_reason(method, connection.url.path, dict(connection.query_params))
            if reason is not None:
                record_scope_denial(reason, project_scope)
                await self._refuse(scope, receive, send)
                return
        with bind_project_scope(project_scope):
            await self._app(scope, receive, send)

    async def _resolve(self, scope: Scope) -> ProjectScope | None:
        from omnigent.server.agent_account_tokens import is_agent_token
        from omnigent.server.auth import _agent_account_principal, _bearer_token

        connection = HTTPConnection(scope)
        token = _bearer_token(connection)
        if token is None or not is_agent_token(token):
            return None
        principal = await asyncio.to_thread(_agent_account_principal, connection)
        project_ids = scope.get("mcp_project_scope")
        account_id = scope.get("mcp_account_id")
        if not isinstance(principal, str) or project_ids is None:
            return None
        return ProjectScope(
            account_id=account_id if isinstance(account_id, str) else "",
            project_ids=frozenset(project_ids),
            route=connection.url.path,
            conversation_store=self._conversation_store,
        )

    @staticmethod
    async def _refuse(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4403})
            return
        response = JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": ErrorCode.OUT_OF_SCOPE,
                    "message": "Not allowed for this agent account",
                }
            },
        )
        await response(scope, receive, send)
