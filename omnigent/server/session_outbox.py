"""Produce durable session-lifecycle events for the manager webhook.

Writes happen only while ``manager_webhook.enabled`` is true, so a default
install does not grow the outbox. A successful insert wakes the dispatcher
immediately; HTTP delivery stays on that background task.

``session.completed``, ``session.failed``, ``session.awaiting_decision``, and
``session.resumed`` use the upstream event names. ``session.decision_resolved``
is a fork extension emitted when a human verdict is recorded, so a receiver
can retract a prompt before the runner has consumed it.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from omnigent.db.db_models import current_workspace_id
from omnigent.db.utils import now_epoch
from omnigent.entities import SessionElicitation
from omnigent.runtime import pending_elicitations
from omnigent.runtime.telemetry import _REDACT_KEY_SUBSTRINGS
from omnigent.server.server_config import (
    MANAGER_WEBHOOK_EVENT_TYPES,
    ManagerWebhookConfig,
    ManagerWebhookConfigError,
    manager_webhook_config,
)
from omnigent.stores.session_lifecycle_store import SessionLifecycleStore

_logger = logging.getLogger(__name__)

_EVENT_ID_NAMESPACE = uuid.UUID("6f1b7c3a-8d2e-4a5f-9c1b-2e3f4a5b6c7d")

_ELICITATION_REQUEST_ALLOWLIST = (
    "mode",
    "message",
    "requestedSchema",
    "url",
    "phase",
    "policy_name",
    "target_session_id",
    "tool_name",
)

_ELICITATION_DECISION_ALLOWLIST = (
    "action",
    "content",
)

_SAFE_FAILURE_MESSAGES: dict[str, str] = {
    "runner_disconnected": "The runner disconnected unexpectedly.",
    "runner_failed_to_start": "The runner failed to start.",
    "runner_error": "The runner reported an error.",
    "runner_rejected_event": "The runner rejected an event for this turn.",
    "harness_not_configured": "The agent's harness is not configured on the selected host.",
    "codex_turn_error": "The turn failed on the harness side.",
    "codex_reauth_required": "Re-authentication is required for this session.",
    "unknown": "The session turn failed.",
}
_DEFAULT_SAFE_FAILURE_MESSAGE = "The session turn failed."

_SECRET_VALUE_RE = re.compile(
    r"(?i)"
    r"(?:bearer\s+)[^\s]+"
    r"|sk-[a-z0-9_\-]{8,}"
    r"|ghp_[a-z0-9]+"
    r"|github_pat_[a-z0-9_]+"
    r"|xox[baprs]-[a-z0-9\-]+"
    r"|AKIA[0-9A-Z]{8,}"
    r"|(?:postgres|postgresql|mysql|mongodb|redis|amqp)://\S+"
    r"|(?:api[_-]?key|token|secret|password)\s*[=:]\s*\S+"
)

_RESOLVE_ACTIONS = ("accept", "decline", "cancel")

ContextLookup = Callable[[str], "SessionPushContext | None"]
SummaryLookup = Callable[[str], str | None]

_store: SessionLifecycleStore | None = None
_context_lookup: ContextLookup | None = None
_summary_lookup: SummaryLookup | None = None


@dataclass(frozen=True)
class SessionPushContext:
    """Session fields copied into an awaiting-decision payload."""

    title: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    agent_name: str | None = None


@dataclass(frozen=True)
class DecisionWrite:
    """Result of persisting a human verdict.

    :param elicitation: Ledger row after the write.
    :param ownership_mismatch: True when the row belongs to another session.
        The verdict is left untouched in that case.
    """

    elicitation: SessionElicitation | None = None
    ownership_mismatch: bool = False


def configure(
    store: SessionLifecycleStore | None,
    *,
    context_lookup: ContextLookup | None = None,
    summary_lookup: SummaryLookup | None = None,
) -> None:
    """Wire the store and the publish-path listener, or clear both."""
    global _store, _context_lookup, _summary_lookup
    _store = store
    _context_lookup = context_lookup
    _summary_lookup = summary_lookup
    if store is None:
        pending_elicitations.remove_elicitation_lifecycle_listener(_on_elicitation_event)
        return
    pending_elicitations.add_elicitation_lifecycle_listener(_on_elicitation_event)


def reset_for_tests() -> None:
    """Clear the process-global store pointer between tests."""
    configure(None)


def _active_config() -> ManagerWebhookConfig | None:
    if _store is None:
        return None
    try:
        config = manager_webhook_config()
    except ManagerWebhookConfigError:
        _logger.warning("manager_webhook config invalid; skipping outbox write", exc_info=True)
        return None
    if not config.enabled:
        return None
    return config


def _notify() -> None:
    # Imported lazily: the dispatcher imports nothing from this module, but
    # keeping the edge one-way avoids an import cycle if that changes.
    from omnigent.server.manager_webhook_dispatcher import request_delivery

    request_delivery()


def _event_id(workspace_id: int, session_id: str, transition_key: str) -> str:
    name = f"{workspace_id}:{session_id}:{transition_key}"
    return uuid.uuid5(_EVENT_ID_NAMESPACE, name).hex


def _allowlist(source: dict[str, Any], allowed: tuple[str, ...]) -> dict[str, Any]:
    return {key: source[key] for key in allowed if key in source and source[key] is not None}


def _redact_denylist(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (
            "[redacted]" if any(token in key.lower() for token in _REDACT_KEY_SUBSTRINGS) else item
        )
        for key, item in value.items()
    }


def redact_summary(text: str, *, max_chars: int) -> str:
    """Truncate *text* and replace credential-shaped substrings."""
    cleaned = _SECRET_VALUE_RE.sub("[redacted]", text).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    if max_chars <= 1:
        return "…"
    return cleaned[: max_chars - 1] + "…"


def _lookup_context(session_id: str) -> SessionPushContext:
    lookup = _context_lookup
    if lookup is None:
        return SessionPushContext()
    try:
        found = lookup(session_id)
    except Exception:  # context is optional; the outbox write still proceeds
        _logger.exception("manager webhook session context lookup failed for %s", session_id)
        return SessionPushContext()
    return found if found is not None else SessionPushContext()


def _lookup_summary(session_id: str, *, max_chars: int) -> str | None:
    lookup = _summary_lookup
    if lookup is None:
        return None
    try:
        text = lookup(session_id)
    except Exception:  # summary is optional; the outbox write still proceeds
        _logger.exception("manager webhook summary lookup failed for %s", session_id)
        return None
    if not text:
        return None
    return redact_summary(text, max_chars=max_chars)


def _resolve_descriptor(
    *,
    session_id: str,
    elicitation_id: str,
    public_base_url: str | None,
) -> dict[str, Any]:
    path = f"/v1/sessions/{session_id}/elicitations/{elicitation_id}/resolve"
    body: dict[str, Any] = {
        "method": "POST",
        "path": path,
        "body": {"action": "accept", "content": {}},
        "actions": list(_RESOLVE_ACTIONS),
    }
    if public_base_url:
        body["url"] = f"{public_base_url}{path}"
    return body


def build_awaiting_decision_data(
    *,
    session_id: str,
    elicitation_id: str,
    request: dict[str, Any],
    created_at: int,
    context: SessionPushContext,
    public_base_url: str | None,
) -> dict[str, Any]:
    """Self-contained ``session.awaiting_decision`` ``data`` object.

    ``request`` keeps the upstream allowlist so an existing receiver still
    finds ``data.request.message``. The surrounding fields are the fork's
    richer context: title, project, agent, schema, and the resolve route.
    """
    allowlisted = _allowlist(request, _ELICITATION_REQUEST_ALLOWLIST)
    target = allowlisted.get("target_session_id")
    resolve_session = target if isinstance(target, str) and target else session_id
    data: dict[str, Any] = {
        "elicitation_id": elicitation_id,
        "created_at": created_at,
        "session_id": session_id,
        "target_session_id": resolve_session,
        "session_title": context.title,
        "project_id": context.project_id,
        "project_name": context.project_name,
        "agent_name": context.agent_name,
        "message": allowlisted.get("message"),
        "requestedSchema": allowlisted.get("requestedSchema"),
        "resolve": _resolve_descriptor(
            session_id=resolve_session,
            elicitation_id=elicitation_id,
            public_base_url=public_base_url,
        ),
        "request": allowlisted,
    }
    if public_base_url:
        data["web_url"] = f"{public_base_url}/approve/{resolve_session}/{elicitation_id}"
    return {key: value for key, value in data.items() if value is not None}


def build_completed_data(
    *,
    response_id: str,
    background_task_count: int | None,
    summary: str | None,
) -> dict[str, Any]:
    """``session.completed`` data. ``summary`` is omitted when empty."""
    payload: dict[str, Any] = {"response_id": response_id}
    if background_task_count is not None:
        payload["background_task_count"] = background_task_count
    if summary:
        payload["summary"] = summary
    return payload


def build_failed_data(
    *,
    response_id: str,
    error_code: str,
    error_message: str,
    summary_max_chars: int,
) -> dict[str, Any]:
    """``session.failed`` data.

    ``reason.message`` is a fixed safe string chosen by ``error_code``.
    ``summary`` is the truncated, credential-redacted error text when one
    was supplied.
    """
    safe_message = _SAFE_FAILURE_MESSAGES.get(error_code, _DEFAULT_SAFE_FAILURE_MESSAGE)
    reason = _redact_denylist({"code": str(error_code)[:64], "message": safe_message})
    payload: dict[str, Any] = {"response_id": response_id, "reason": reason}
    if error_message:
        payload["summary"] = redact_summary(error_message, max_chars=summary_max_chars)
    return payload


def _record(
    *,
    config: ManagerWebhookConfig,
    session_id: str,
    event_type: str,
    transition_key: str,
    payload: dict[str, Any],
    now: int,
) -> bool:
    store = _store
    if store is None or event_type not in MANAGER_WEBHOOK_EVENT_TYPES:
        return False
    if not config.wants(event_type):
        return False
    workspace_id = current_workspace_id()
    _event, inserted = store.record_lifecycle_event(
        event_id=_event_id(workspace_id, session_id, transition_key),
        session_id=session_id,
        event_type=event_type,
        transition_key=transition_key,
        payload=json.dumps(payload, separators=(",", ":"), default=str),
        now=now,
    )
    if inserted:
        _notify()
    return inserted


def record_session_completed(
    *,
    session_id: str,
    response_id: str,
    background_task_count: int | None = None,
    summary: str | None = None,
) -> None:
    """Record ``session.completed`` for a turn that just went idle."""
    config = _active_config()
    if config is None:
        return
    text = summary
    if text is None:
        text = _lookup_summary(session_id, max_chars=config.summary_max_chars)
    elif text:
        text = redact_summary(text, max_chars=config.summary_max_chars)
    _record(
        config=config,
        session_id=session_id,
        event_type="session.completed",
        transition_key=f"turn:{response_id}:completed",
        payload=build_completed_data(
            response_id=response_id,
            background_task_count=background_task_count,
            summary=text,
        ),
        now=now_epoch(),
    )


def record_session_failed(
    *,
    session_id: str,
    response_id: str,
    error_code: str,
    error_message: str,
) -> None:
    """Record ``session.failed``. The raw error is redacted before it is stored."""
    config = _active_config()
    if config is None:
        return
    if error_message:
        _logger.info(
            "session.failed diagnostic (delivery uses a redacted summary): session_id=%s code=%s",
            session_id,
            error_code,
        )
    _record(
        config=config,
        session_id=session_id,
        event_type="session.failed",
        transition_key=f"turn:{response_id}:failed",
        payload=build_failed_data(
            response_id=response_id,
            error_code=error_code,
            error_message=error_message,
            summary_max_chars=config.summary_max_chars,
        ),
        now=now_epoch(),
    )


def _request_from_event(event: dict[str, Any]) -> dict[str, Any]:
    params = event.get("params")
    request = dict(params) if isinstance(params, dict) else {}
    tool_name = event.get("tool_name")
    if isinstance(tool_name, str) and tool_name:
        request.setdefault("tool_name", tool_name)
    return request


def record_elicitation_raised(
    *,
    session_id: str,
    elicitation_id: str,
    request: dict[str, Any],
) -> None:
    """Record the ledger row and ``session.awaiting_decision`` together."""
    config = _active_config()
    store = _store
    if config is None or store is None or not config.wants("session.awaiting_decision"):
        return
    now = now_epoch()
    context = _lookup_context(session_id)
    allowlisted = _allowlist(request, _ELICITATION_REQUEST_ALLOWLIST)
    data = build_awaiting_decision_data(
        session_id=session_id,
        elicitation_id=elicitation_id,
        request=request,
        created_at=now,
        context=context,
        public_base_url=config.public_base_url,
    )
    workspace_id = current_workspace_id()
    transition_key = f"elicitation:{elicitation_id}:awaiting_decision"
    _elicitation, _event, inserted = store.record_elicitation_raised(
        elicitation_id=elicitation_id,
        session_id=session_id,
        request_payload=json.dumps(allowlisted, separators=(",", ":"), default=str),
        outbox_event_id=_event_id(workspace_id, session_id, transition_key),
        transition_key=transition_key,
        outbox_payload=json.dumps(data, separators=(",", ":"), default=str),
        now=now,
    )
    if inserted:
        _notify()


def record_decision(
    *,
    elicitation_id: str,
    session_id: str,
    decision: dict[str, Any],
    decided_by: str | None = None,
) -> DecisionWrite:
    """Persist a verdict and emit ``session.decision_resolved`` on the first write.

    A ledger row owned by a different session is left unchanged and reported
    as an ownership mismatch.
    """
    config = _active_config()
    store = _store
    if config is None or store is None or not elicitation_id:
        return DecisionWrite()
    existing = store.get_elicitation(elicitation_id)
    if existing is not None and existing.session_id != session_id:
        return DecisionWrite(elicitation=existing, ownership_mismatch=True)
    allowlisted = _allowlist(decision, _ELICITATION_DECISION_ALLOWLIST)
    now = now_epoch()
    transition_key = f"elicitation:{elicitation_id}:decision_resolved"
    workspace_id = current_workspace_id()
    wants_event = config.wants("session.decision_resolved")
    payload = {
        "elicitation_id": elicitation_id,
        "session_id": session_id,
        "target_session_id": session_id,
        "decision": allowlisted,
        "fork_extension": True,
    }
    row = store.record_decision(
        elicitation_id,
        decision_payload=json.dumps(allowlisted, separators=(",", ":"), default=str),
        decided_by=decided_by,
        now=now,
        outbox_event_id=_event_id(workspace_id, session_id, transition_key)
        if wants_event
        else None,
        outbox_transition_key=transition_key if wants_event else None,
        outbox_payload=(
            json.dumps(payload, separators=(",", ":"), default=str) if wants_event else None
        ),
    )
    if row is not None and row.decided_at == now and wants_event:
        _notify()
    return DecisionWrite(elicitation=row)


def record_elicitation_resumed(*, elicitation_id: str) -> bool:
    """Record ``session.resumed`` once the runner or harness consumed the verdict."""
    config = _active_config()
    store = _store
    if config is None or store is None or not config.wants("session.resumed"):
        return False
    existing = store.get_elicitation(elicitation_id)
    if existing is None:
        return False
    decision_payload = json.loads(existing.decision_payload) if existing.decision_payload else {}
    now = now_epoch()
    transition_key = f"elicitation:{elicitation_id}:resumed"
    workspace_id = current_workspace_id()
    _row, _event, inserted = store.record_elicitation_resolved(
        elicitation_id,
        outbox_event_id=_event_id(workspace_id, existing.session_id, transition_key),
        transition_key=transition_key,
        outbox_payload=json.dumps(
            {"elicitation_id": elicitation_id, "decision": decision_payload},
            separators=(",", ":"),
            default=str,
        ),
        resolved_at=now,
    )
    if inserted:
        _notify()
    return inserted


def _owning_session(conversation_id: str, request: dict[str, Any]) -> str:
    target = request.get("target_session_id")
    if isinstance(target, str) and target:
        return target
    return conversation_id


def _on_elicitation_event(conversation_id: str, event: dict[str, Any]) -> None:
    """Publish-path listener. Does not replace the sub-agent observer slot."""
    event_type = event.get("type")
    elicitation_id = event.get("elicitation_id")
    if not isinstance(elicitation_id, str) or not elicitation_id:
        return
    if event_type == "response.elicitation_request":
        request = _request_from_event(event)
        owner = _owning_session(conversation_id, request)
        # Ancestor mirrors carry target_session_id of the child. Record once,
        # against the session that owns the resolve route.
        if owner != conversation_id:
            return
        record_elicitation_raised(
            session_id=conversation_id,
            elicitation_id=elicitation_id,
            request=request,
        )
        return
    if event_type == "response.elicitation_resolved":
        action = event.get("action")
        decision: dict[str, Any] = {}
        if isinstance(action, str) and action in _RESOLVE_ACTIONS:
            decision["action"] = action
        else:
            decision["action"] = "cancel"
        content = event.get("content")
        if isinstance(content, dict):
            decision["content"] = content
        record_decision(
            elicitation_id=elicitation_id,
            session_id=conversation_id,
            decision=decision,
        )
