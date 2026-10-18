"""Rebind an idle session's workspace and host.

Shared by ``PATCH /v1/sessions/{id}``. The MCP ``manage_session`` update
action calls that route, so dry-run and confirm stay on the existing
write path.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from omnigent.db.db_models import InvalidUuidError, uuid_to_bytes
from omnigent.debug_logging import add_audit_attrs, debug_event
from omnigent.entities import Conversation
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.mcp_endpoint import _COMPOSER_LABEL_PREFIX
from omnigent.server.schemas import is_absolute_workspace_path
from omnigent.stores import AgentStore, ConversationStore
from omnigent.stores.conversation_store import SessionBindResult
from omnigent.stores.host_store import HostStore

_logger = logging.getLogger(__name__)

# Matches the web client's composer-context chunk size, under the 256-char
# label value limit.
_COMPOSER_CHUNK = 240


@dataclass(frozen=True)
class SessionBindPlan:
    """A validated workspace/host change, ready to persist.

    :param host_id: Host to write, or ``None`` to leave the host unchanged.
    :param workspace: Absolute path to store. A live host's canonical path
        replaces the path the caller sent.
    :param expected_runner_id: Runner id observed before the host stat.
        The store refuses a real change when the locked row has moved on.
    :param old_host_id: Host on the row before this rebind.
    :param old_workspace: Workspace on the row before this rebind.
    """

    host_id: str | None
    workspace: str
    expected_runner_id: str | None
    old_host_id: str | None
    old_workspace: str | None


def bot_may_rebind(
    *,
    actor: str | None,
    owner_id: str | None,
    session_id: str,
    agent_id: str | None,
) -> bool:
    """Whether an agent-account may rebind this session.

    The default allows it once the caller has already passed the session
    owner gate (a bot acts as its owner). Replace this function to scope
    bots to particular agents or sessions.

    :param actor: ``mcp_actor`` principal, e.g. ``"agent:acct_abc"``.
    :param owner_id: The bot's owner, when the request carried one.
    :param session_id: Session being rebound.
    :param agent_id: Agent bound to that session, or ``None``.
    :returns: ``True`` when the rebind may proceed.
    """
    del actor, owner_id, session_id, agent_id
    return True


def composer_label_updates(
    labels: Mapping[str, str], path: str
) -> tuple[dict[str, str], list[str]]:
    """Rewrite the composer working directory and name leftover chunk keys.

    The web client stores ``omnigent.composer_context.v1`` JSON, split into
    ``.0``, ``.1``, … . The unchunked key is removed so a stale copy cannot
    hide the new chunks.

    :param labels: Current session labels.
    :param path: Workspace path to store under ``working_directory.path``.
    :returns: Labels to upsert, and composer keys to delete.
    """
    prefix = _COMPOSER_LABEL_PREFIX
    raw = _composer_raw(labels, prefix)
    parsed: Any
    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        doc = dict(parsed)
        working = doc.get("working_directory")
        if isinstance(working, dict):
            updated = dict(working)
            updated["path"] = path
            doc["working_directory"] = updated
        else:
            doc["working_directory"] = {"path": path}
        if "version" not in doc:
            doc["version"] = 1
    else:
        doc = {
            "version": 1,
            "working_directory": {"path": path},
            "worktree": {"mode": "none"},
        }
    encoded = json.dumps(doc, separators=(",", ":"), ensure_ascii=False)
    upserts: dict[str, str] = {}
    for index, offset in enumerate(range(0, len(encoded), _COMPOSER_CHUNK)):
        upserts[f"{prefix}.{index}"] = encoded[offset : offset + _COMPOSER_CHUNK]
    chunk_prefix = f"{prefix}."
    stale: list[str] = []
    if prefix in labels:
        stale.append(prefix)
    for key in labels:
        if not key.startswith(chunk_prefix) or key in upserts:
            continue
        if key[len(chunk_prefix) :].isdigit():
            stale.append(key)
    return upserts, stale


def _composer_raw(labels: Mapping[str, str], prefix: str) -> str:
    """Join composer chunks, preferring a single unchunked value."""
    raw = labels.get(prefix)
    if isinstance(raw, str) and raw:
        return raw
    chunk_prefix = f"{prefix}."
    chunks: list[tuple[int, str]] = []
    for key, value in labels.items():
        if not key.startswith(chunk_prefix) or not isinstance(value, str):
            continue
        suffix = key[len(chunk_prefix) :]
        if suffix.isdigit():
            chunks.append((int(suffix), value))
    return "".join(value for _, value in sorted(chunks))


async def prepare_session_bind(
    *,
    user_id: str | None,
    session_id: str,
    conv: Conversation,
    fields_set: set[str],
    workspace: str | None,
    host_id: str | None,
    host_store: HostStore | None,
    host_registry: Any,
    agent_store: AgentStore | None,
    agent_cache: AgentCache | None,
    actor: str | None,
    actor_owner_id: str | None,
) -> SessionBindPlan | None:
    """Validate a workspace/host rebind without writing.

    An omitted field is left unchanged. An explicit null is rejected by
    the request schema (422) before this runs. A running or archived
    session is rejected before any host probe. A host must be connected
    here; the session's agent supplies the ``os_env.cwd`` boundary.

    :param user_id: Authenticated caller, or ``None`` when auth is off.
        A bot's in-process call is its owner.
    :param session_id: Session being updated.
    :param conv: Conversation row observed before the host stat.
    :param fields_set: Names present on the PATCH body.
    :param workspace: Requested workspace, or ``None`` when omitted.
    :param host_id: Requested host, or ``None`` when omitted.
    :param host_store: Persistent host registrations.
    :param host_registry: Live host connections on this replica.
    :param agent_store: Store holding the session's agent row.
    :param agent_cache: Cache used to read ``os_env.cwd``.
    :param actor: ``mcp_actor`` when the caller is an agent account.
    :param actor_owner_id: That account's owner, when present.
    :returns: The plan to persist, or ``None`` when the request does not
        rebind.
    :raises OmnigentError: 409 when the session is running, archived, or
        its host is offline; 400 for a path the host rejects; 403 when
        the host belongs to someone else or a bot hook refuses; 404 when
        the host is unknown.
    """
    set_workspace = "workspace" in fields_set
    set_host = "host_id" in fields_set
    if not set_workspace and not set_host:
        return None
    if set_workspace and workspace is None:
        raise OmnigentError(
            "workspace must be an absolute path",
            code=ErrorCode.INVALID_INPUT,
        )
    if set_host and (host_id is None or not host_id.strip()):
        raise OmnigentError(
            "host_id must be a non-empty host id",
            code=ErrorCode.INVALID_INPUT,
        )
    if conv.archived:
        raise OmnigentError(
            "Cannot change the workspace or host of an archived session",
            code=ErrorCode.CONFLICT,
        )
    if _session_is_running(session_id, conv.live_status):
        raise OmnigentError(
            "Cannot change the workspace or host while the session is running",
            code=ErrorCode.CONFLICT,
        )
    if isinstance(actor, str) and actor.startswith("agent:"):
        if not bot_may_rebind(
            actor=actor,
            owner_id=actor_owner_id,
            session_id=session_id,
            agent_id=conv.agent_id,
        ):
            raise OmnigentError(
                "This bot cannot rebind the session workspace or host",
                code=ErrorCode.FORBIDDEN,
            )

    target_workspace = workspace if set_workspace else conv.workspace
    normalized_host: str | None = None
    if set_host:
        assert host_id is not None
        normalized_host = _known_host_id(host_id)
    target_host = normalized_host if set_host else conv.host_id
    if target_workspace is None or not is_absolute_workspace_path(target_workspace):
        if target_host is not None and not target_workspace:
            raise OmnigentError(
                "workspace required when host_id is set",
                code=ErrorCode.INVALID_INPUT,
            )
        raise OmnigentError(
            "workspace must be an absolute path",
            code=ErrorCode.INVALID_INPUT,
        )

    stored_workspace = target_workspace
    if target_host is not None:
        stored_workspace = await _workspace_on_host(
            user_id=user_id,
            host_id=target_host,
            workspace=target_workspace,
            conv=conv,
            agent_store=agent_store,
            agent_cache=agent_cache,
            host_store=host_store,
            host_registry=host_registry,
        )

    return SessionBindPlan(
        host_id=normalized_host if set_host else None,
        workspace=stored_workspace,
        expected_runner_id=conv.runner_id,
        old_host_id=conv.host_id,
        old_workspace=conv.workspace,
    )


def apply_session_bind(
    conversation_store: ConversationStore,
    session_id: str,
    plan: SessionBindPlan,
    expected_runner_id: str | None,
) -> SessionBindResult:
    """Persist a prepared bind and rewrite the composer working directory.

    The store re-checks ``live_status`` and ``expected_runner_id`` under
    the write lock. This also refuses when this replica's status cache
    already shows a turn, which can be ahead of that row.

    :param conversation_store: Store holding the session row and labels.
    :param session_id: Session to update.
    :param plan: Validated bind from :func:`prepare_session_bind`.
    :param expected_runner_id: Runner id to compare. The route passes the
        pre-stat id, or the id it wrote earlier in this same PATCH.
    :returns: The store's compare-and-set outcome.
    :raises OmnigentError: ``conflict`` when a turn started after validation
        or the locked row refuses the write.
    """
    if _cached_turn(session_id):
        raise OmnigentError(
            "Cannot change the workspace or host while the session is running",
            code=ErrorCode.CONFLICT,
        )
    return conversation_store.bind_session_workspace(
        session_id,
        host_id=plan.host_id,
        workspace=plan.workspace,
        expected_runner_id=expected_runner_id,
        label_plan=lambda labels: composer_label_updates(labels, plan.workspace),
    )


def record_session_rebind(
    *,
    session_id: str,
    actor: str | None,
    owner_id: str | None,
    old_host_id: str | None,
    new_host_id: str | None,
    old_workspace: str | None,
    new_workspace: str,
) -> None:
    """Record a ``session_rebind`` audit event for a binding that changed.

    :param session_id: Session that was rebound.
    :param actor: ``mcp_actor`` principal, or ``None`` for a human caller.
    :param owner_id: Session owner.
    :param old_host_id: Host before the write.
    :param new_host_id: Host after the write.
    :param old_workspace: Workspace before the write.
    :param new_workspace: Canonical workspace that was stored.
    """
    add_audit_attrs(
        event="session_rebind",
        mcp_actor=actor,
        owner=owner_id,
        old_host_id=old_host_id,
        new_host_id=new_host_id,
        old_workspace=old_workspace,
        new_workspace=new_workspace,
        session_id=session_id,
    )
    _logger.info(
        "Session workspace rebound",
        extra=debug_event(
            "session_rebind",
            session_id=session_id,
            mcp_actor=actor,
            owner=owner_id,
            old_host_id=old_host_id,
            new_host_id=new_host_id,
            old_workspace=old_workspace,
            new_workspace=new_workspace,
        ),
    )


def _session_is_running(session_id: str, db_status: str | None) -> bool:
    """Whether the relay cache or the row says the session is in a turn."""
    from omnigent.server.routes._sessions.helpers import _session_status_from_cache

    return _session_status_from_cache(session_id, db_status) == "running"


def _cached_turn(session_id: str) -> bool:
    """Whether this replica's status cache shows a turn, ignoring the row.

    The cache is updated before the database write is flushed, so it
    closes the gap after a host stat returns.
    """
    from omnigent.server.routes._sessions.common import _session_status_cache

    return _session_status_cache.get(session_id) in ("running", "waiting")


def _known_host_id(host_id: str) -> str:
    """Return the bare-hex host id, or 404 when it is not a host id."""
    try:
        return uuid_to_bytes(host_id).hex()
    except InvalidUuidError:
        raise OmnigentError("host not found", code=ErrorCode.NOT_FOUND) from None


async def _workspace_on_host(
    *,
    user_id: str | None,
    host_id: str,
    workspace: str,
    conv: Conversation,
    agent_store: AgentStore | None,
    agent_cache: AgentCache | None,
    host_store: HostStore | None,
    host_registry: Any,
) -> str:
    """Validate ``workspace`` with the session's agent, as session create does.

    An offline host is ``conflict`` (409). A host that is live on another
    replica is ``wrong_replica``. The agent's ``os_env.cwd`` supplies the
    boundary, the ``./subdir`` check, and canonicalization.

    :returns: The host's canonical workspace path.
    :raises OmnigentError: On ownership, replica, offline, or path failure.
    """
    import asyncio

    from omnigent.server.routes._session_create_validation import (
        validate_existing_host_workspace,
    )

    agent: Any = None
    if conv.agent_id and agent_store is not None:
        agent = await asyncio.to_thread(agent_store.get, conv.agent_id)
    if agent is None:
        # The spec is gone, so there is no cwd boundary left to enforce.
        # Existence, directory, and canonicalization still run.
        agent = SimpleNamespace(id=conv.agent_id or "unbound", bundle_location=None)
    try:
        canonical = await validate_existing_host_workspace(
            user_id=user_id,
            host_id=host_id,
            workspace=workspace,
            agent=agent,
            agent_cache=agent_cache,
            host_store=host_store,
            host_registry=host_registry,
        )
    except OmnigentError as exc:
        if exc.code == ErrorCode.INVALID_INPUT and "is offline" in exc.message:
            raise OmnigentError(exc.message, code=ErrorCode.CONFLICT) from exc
        raise
    return canonical
