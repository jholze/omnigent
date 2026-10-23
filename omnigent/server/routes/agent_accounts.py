"""Agent-account management routes.

One set of handlers is registered twice: on an ungated router at
``/v1/agent-accounts`` (FR-112), and on the team-chat router at
``/v1/chat/agent-accounts``, which keeps the chat router's feature gate.
Agent accounts authenticate ``/mcp`` and are not a chat feature, so this
module never reads a feature flag.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable

from fastapi import APIRouter, Request
from pydantic import BaseModel

from omnigent.debug_logging import add_audit_attrs
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.agent_account_tokens import (
    agent_account_secret,
    derive_webhook_secret,
    webhook_url_rejected,
)
from omnigent.server.auth import RESERVED_USER_LOCAL, AuthProvider
from omnigent.server.chat_access import principal_kind
from omnigent.server.routes._auth_helpers import require_user
from omnigent.stores.chat_store import AccountStatus, ChatAgentAccount, ChatStore

_HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
_PROJECT_ID_RE = re.compile(r"^[0-9a-f]{32}$")

CallerResolver = Callable[[Request], str]
AuditAttrs = Callable[..., None]


class AgentAccountBody(BaseModel):
    """``POST /v1/agent-accounts``."""

    display_name: str
    handle: str
    webhook_url: str | None = None
    # Allowed project ids. Omitted or null is unscoped; an empty list is 400.
    project_ids: list[str] | None = None


class AgentAccountScopeBody(BaseModel):
    """``PATCH /v1/agent-accounts/{id}``.

    ``project_ids`` is required. A list scopes the account; an explicit
    ``null`` removes the scope; an empty list is 400.
    """

    project_ids: list[str] | None


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
    # Allowed project ids, or null when the account is unscoped.
    project_ids: list[str] | None = None


class AgentAccountCreatedResponse(BaseModel):
    """``POST /v1/agent-accounts``. Token and webhook secret are shown once."""

    account: AgentAccountResponse
    token: str
    webhook_secret: str


class AgentAccountRotatedResponse(BaseModel):
    """``POST /v1/agent-accounts/{id}/rotate``. The previous token is dead."""

    account: AgentAccountResponse
    token: str
    webhook_secret: str


def resolve_caller(request: Request, auth_provider: AuthProvider | None) -> str:
    """Resolve the caller principal, falling back to the local user.

    An in-process call from ``/mcp`` authenticates as the owner; the agent
    account the auth branch stored as ``mcp_actor`` is returned instead.

    :raises OmnigentError: 401 when multi-user auth is active but the
        request carries no identity.
    """
    actor = request.scope.get("mcp_actor")
    if request.scope.get("mcp_inprocess") is True and isinstance(actor, str) and actor:
        return actor
    user_id = require_user(request, auth_provider)
    return user_id if user_id is not None else RESERVED_USER_LOCAL


def _require_human(caller: str) -> None:
    """Refuse agent accounts on routes that mint or rotate credentials."""
    if principal_kind(caller) == "agent_account":
        raise OmnigentError(
            "agent accounts cannot manage agent accounts",
            code=ErrorCode.FORBIDDEN,
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
        project_ids=list(account.project_ids) if account.project_ids is not None else None,
    )


def _validate_project_ids(raw: list[str] | None) -> tuple[str, ...] | None:
    """Normalize an allow-list. ``None`` stays unscoped (FR-111).

    :raises OmnigentError: 400 for an empty list, 404 for a malformed id.
    """
    if raw is None:
        return None
    if not raw:
        raise OmnigentError(
            "project_ids must not be empty; send null to remove the scope",
            code=ErrorCode.INVALID_INPUT,
        )
    normalized = tuple(sorted({item.strip().lower() for item in raw}))
    if any(not _PROJECT_ID_RE.fullmatch(item) for item in normalized):
        raise OmnigentError("Project not found", code=ErrorCode.NOT_FOUND)
    return normalized


def _require_agent_secret() -> None:
    """Refuse account minting when the HMAC secret is unset (FR-061)."""
    if agent_account_secret() is None:
        raise OmnigentError(
            "agent accounts are not configured",
            code=ErrorCode.AGENT_ACCOUNTS_UNCONFIGURED,
        )


def _default_audit_attrs(**attrs: object) -> None:
    """Forward to :func:`add_audit_attrs`, looked up at call time."""
    add_audit_attrs(**attrs)


def register_agent_account_routes(
    router: APIRouter,
    chat_store: ChatStore,
    *,
    base_path: str,
    resolve_caller: CallerResolver,
    audit_attrs: AuditAttrs = _default_audit_attrs,
) -> None:
    """Register list, create, rotate, scope PATCH, revoke, and delete on *router*.

    :param base_path: Collection path relative to the router prefix,
        e.g. ``/agent-accounts``.
    :param resolve_caller: Maps a request to the caller principal.
    :param audit_attrs: Sink for audit attributes. Never receives the
        token, webhook secret, or webhook URL.
    """

    def _audit_account(
        action: str, *, account_id: str | None, owner_id: str, outcome: str
    ) -> None:
        """Record an account change without the token, secret, or webhook URL."""
        audit_attrs(action=action, account_id=account_id, owner_id=owner_id, outcome=outcome)

    @router.get(base_path, response_model=list[AgentAccountResponse])
    async def list_agent_accounts(request: Request) -> list[AgentAccountResponse]:
        """List active agent accounts owned by the caller.

        Another human's accounts are not included. ``last_seen_at`` is
        epoch microseconds, or null when the account has never called.
        """
        caller = resolve_caller(request)
        _require_human(caller)
        accounts = await asyncio.to_thread(chat_store.list_active_agent_accounts)
        owned = [account for account in accounts if account.owner_id == caller]
        return [_agent_account_response(account) for account in owned]

    @router.post(base_path, response_model=AgentAccountCreatedResponse, status_code=201)
    async def create_agent_account(
        request: Request, body: AgentAccountBody
    ) -> AgentAccountCreatedResponse:
        """Mint an agent account. The raw ``ogc_`` token is returned once."""
        caller = resolve_caller(request)
        _require_human(caller)
        _require_agent_secret()
        display_name = _validate_display_name(body.display_name)
        handle = _validate_handle(body.handle)
        project_ids = _validate_project_ids(body.project_ids)
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
            project_ids=project_ids,
        )
        if result.status is AccountStatus.HANDLE_TAKEN:
            raise OmnigentError("handle already exists", code=ErrorCode.CONFLICT)
        if result.status is AccountStatus.PROJECT_NOT_FOUND:
            raise OmnigentError("Project not found", code=ErrorCode.NOT_FOUND)
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
        base_path + "/{account_id}/rotate",
        response_model=AgentAccountRotatedResponse,
    )
    async def rotate_agent_account(
        request: Request, account_id: str
    ) -> AgentAccountRotatedResponse:
        """Rotate the token. The previous ``ogc_`` value returns 401."""
        caller = resolve_caller(request)
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

    @router.patch(base_path + "/{account_id}", response_model=AgentAccountResponse)
    async def update_agent_account_scope(
        request: Request, account_id: str, body: AgentAccountScopeBody
    ) -> AgentAccountResponse:
        """Replace the project allow-list (FR-111). Every refusal leaves it unchanged.

        ``[]`` is 400. A project the caller does not own is 404. ``null``
        returns the account to unscoped owner rights.
        """
        caller = resolve_caller(request)
        _require_human(caller)
        project_ids = _validate_project_ids(body.project_ids)
        if not _PROJECT_ID_RE.fullmatch(account_id):
            raise OmnigentError("agent account not found", code=ErrorCode.NOT_FOUND)
        result = await asyncio.to_thread(
            chat_store.set_agent_account_projects, account_id, caller, project_ids
        )
        if result.status is AccountStatus.NOT_FOUND:
            raise OmnigentError("agent account not found", code=ErrorCode.NOT_FOUND)
        if result.status is AccountStatus.REVOKED_ACCOUNT:
            raise OmnigentError("agent account is revoked", code=ErrorCode.CONFLICT)
        if result.status is AccountStatus.PROJECT_NOT_FOUND:
            raise OmnigentError("Project not found", code=ErrorCode.NOT_FOUND)
        if result.account is None:
            raise OmnigentError("agent account was not updated", code=ErrorCode.INTERNAL_ERROR)
        _audit_account(
            "chat.agent_account.scope",
            account_id=result.account.id,
            owner_id=caller,
            outcome="scoped" if project_ids is not None else "unscoped",
        )
        return _agent_account_response(result.account)

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

    @router.post(base_path + "/{account_id}/revoke", status_code=204)
    async def revoke_agent_account(request: Request, account_id: str) -> None:
        """Revoke the account. Its current token returns 401."""
        caller = resolve_caller(request)
        _require_human(caller)
        await _revoke(caller, account_id)

    @router.delete(base_path + "/{account_id}", status_code=204)
    async def delete_agent_account(request: Request, account_id: str) -> None:
        """Revoke the account (FR-064). A non-owner receives 404."""
        caller = resolve_caller(request)
        _require_human(caller)
        await _revoke(caller, account_id)


def create_agent_accounts_router(
    chat_store: ChatStore,
    *,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the ungated ``/v1/agent-accounts`` router (FR-112).

    An ``ogc_`` bearer never reaches these handlers: the auth middleware
    only accepts it on ``/mcp`` and ``/v1/chat/*``.

    :param chat_store: Store that owns agent accounts.
    :param auth_provider: Identifies the caller. ``None`` in single-user mode.
    """

    router = APIRouter()
    register_agent_account_routes(
        router,
        chat_store,
        base_path="/agent-accounts",
        resolve_caller=lambda request: resolve_caller(request, auth_provider),
    )
    return router
