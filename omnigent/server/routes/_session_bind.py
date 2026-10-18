"""Rebind an idle session's workspace and host.

Shared by ``PATCH /v1/sessions/{id}``. The MCP ``manage_session`` update
action calls that route, so dry-run and confirm stay on the existing
write path.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from omnigent.db.db_models import InvalidUuidError, uuid_to_bytes
from omnigent.entities import Conversation
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.host_registry import HostRegistry
from omnigent.server.mcp_endpoint import _COMPOSER_LABEL_PREFIX
from omnigent.server.schemas import is_absolute_workspace_path
from omnigent.stores import ConversationStore
from omnigent.stores.host_store import HostStore

# Matches the web client's composer-context chunk size, under the 256-char
# label value limit.
_COMPOSER_CHUNK = 240


@dataclass(frozen=True)
class SessionBindPlan:
    """A validated workspace/host change, ready to persist.

    :param host_id: Host to write, or ``None`` to leave the host unchanged.
    :param workspace: Absolute path to store. A live host's canonical path
        replaces the path the caller sent.
    :param binding_changed: Whether the host or workspace differs from the
        row. Only then is the runner cleared.
    """

    host_id: str | None
    workspace: str
    binding_changed: bool


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
    host_registry: HostRegistry | None,
) -> SessionBindPlan | None:
    """Validate a workspace/host rebind without writing.

    An omitted field is left unchanged. An explicit null does not unbind.
    A running session is rejected before any host probe. An online host is
    asked to stat the path; an offline host skips that check.

    :param user_id: Authenticated caller, or ``None`` when auth is off.
    :param session_id: Session being updated.
    :param conv: Current conversation row.
    :param fields_set: Names present on the PATCH body.
    :param workspace: Requested workspace, or ``None`` when omitted or null.
    :param host_id: Requested host, or ``None`` when omitted or null.
    :param host_store: Persistent host registrations. Required when a host
        is part of the binding.
    :param host_registry: Live host connections on this replica. A miss
        means the host is offline and the path is not stat-ed.
    :returns: The plan to persist, or ``None`` when the request does not
        rebind.
    :raises OmnigentError: 409 when the session is running.
    :raises HTTPException: 403 when the host belongs to someone else, 404
        when it is unknown, 422 when the path or host id is unusable.
    """
    set_workspace = "workspace" in fields_set
    set_host = "host_id" in fields_set
    if not set_workspace and not set_host:
        return None
    if set_workspace and workspace is None:
        raise HTTPException(status_code=422, detail="workspace must be an absolute path")
    if set_host and (host_id is None or not host_id.strip()):
        raise HTTPException(status_code=422, detail="host_id must be a non-empty host id")
    if _session_is_running(session_id, conv.live_status):
        raise OmnigentError(
            "Cannot change the workspace or host while the session is running",
            code=ErrorCode.CONFLICT,
        )

    target_workspace = workspace if set_workspace else conv.workspace
    normalized_host: str | None = None
    if set_host:
        assert host_id is not None
        normalized_host = _known_host_id(host_id)
    target_host = normalized_host if set_host else conv.host_id
    if target_host is not None and not target_workspace:
        raise HTTPException(
            status_code=422,
            detail="workspace is required when binding a host",
        )
    if target_workspace is None or not is_absolute_workspace_path(target_workspace):
        raise HTTPException(status_code=422, detail="workspace must be an absolute path")

    stored_workspace = target_workspace
    if target_host is not None:
        if host_store is None:
            raise HTTPException(status_code=404, detail="host not found")
        from omnigent.server.routes._host_launch import resolve_host_owner

        await asyncio.to_thread(
            resolve_host_owner,
            user_id=user_id,
            host_id=target_host,
            host_store=host_store,
        )
        stored_workspace = await _workspace_on_host(
            host_registry,
            target_host,
            target_workspace,
        )

    host_changed = set_host and normalized_host != conv.host_id
    return SessionBindPlan(
        host_id=normalized_host if set_host else None,
        workspace=stored_workspace,
        binding_changed=host_changed or stored_workspace != conv.workspace,
    )


def apply_session_bind(
    conversation_store: ConversationStore,
    session_id: str,
    plan: SessionBindPlan,
) -> str | None:
    """Persist a prepared bind and rewrite the composer working directory.

    Reloads labels after the metadata write so a composer label set earlier
    in the same PATCH is rewritten to the bound path.

    :param conversation_store: Store holding the session row and labels.
    :param session_id: Session to update.
    :param plan: Validated bind from :func:`prepare_session_bind`.
    :returns: The runner id cleared because the binding changed, or ``None``.
    """
    cleared = conversation_store.bind_session_workspace(
        session_id,
        host_id=plan.host_id,
        workspace=plan.workspace,
    )
    fresh = conversation_store.get_conversation(session_id)
    labels = fresh.labels if fresh is not None else {}
    upserts, stale = composer_label_updates(labels, plan.workspace)
    for key in stale:
        conversation_store.delete_label(session_id, key)
    if upserts:
        conversation_store.set_labels(session_id, upserts)
    return cleared


def _session_is_running(session_id: str, db_status: str | None) -> bool:
    """Whether the relay cache or the row says the session is in a turn."""
    from omnigent.server.routes._sessions.helpers import _session_status_from_cache

    return _session_status_from_cache(session_id, db_status) == "running"


def _known_host_id(host_id: str) -> str:
    """Return the bare-hex host id, or 404 when it is not a host id."""
    try:
        return uuid_to_bytes(host_id).hex()
    except InvalidUuidError:
        raise HTTPException(status_code=404, detail="host not found") from None


async def _workspace_on_host(
    host_registry: HostRegistry | None,
    host_id: str,
    workspace: str,
) -> str:
    """Stat ``workspace`` when the host is connected; otherwise keep it.

    :returns: The host's canonical path when the stat succeeds.
    :raises HTTPException: 422 when the path is missing, not a directory,
        or the stat itself fails.
    """
    conn = host_registry.get(host_id) if host_registry is not None else None
    if host_registry is None or conn is None:
        return workspace
    from omnigent.server.routes._workspace_validation import (
        WorkspaceValidationError,
        _ask_host_stat,
    )

    try:
        result = await _ask_host_stat(host_registry=host_registry, host_conn=conn, path=workspace)
    except WorkspaceValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    if result.get("exists") is not True:
        raise HTTPException(
            status_code=422,
            detail=f"workspace path {workspace!r} does not exist on host {host_id!r}",
        )
    if result.get("type") != "directory":
        raise HTTPException(
            status_code=422,
            detail=f"workspace path {workspace!r} is not a directory on host {host_id!r}",
        )
    canonical = result.get("canonical_path")
    if isinstance(canonical, str) and canonical:
        return canonical
    return workspace
