"""Shared session lifecycle markers and display helpers."""

from __future__ import annotations

import logging
from collections.abc import Mapping

CLOSED_LABEL_KEY = "omnigent.closed"
CLOSED_LABEL_VALUE = "true"
CLOSED_TITLE_INFIX = ":closed:"


def title_without_closed_marker(title: str | None) -> str | None:
    """
    Remove the legacy internal closed marker from a stored title.

    ``sys_session_close`` historically freed the
    ``(parent_conversation_id, title)`` unique slot by rewriting a
    child title from ``"agent:task"`` to
    ``"agent:task:closed:conv_abc123"``. That suffix is persistence
    metadata, not user-facing text.

    :param title: Stored conversation title, e.g.
        ``"researcher:auth:closed:conv_abc123"``.
    :returns: Title without the closed suffix, e.g.
        ``"researcher:auth"``, or the original value when no marker
        is present.
    """
    if title is None:
        return None
    visible, marker, _suffix = title.partition(CLOSED_TITLE_INFIX)
    return visible if marker else title


def has_closed_title_marker(title: str | None) -> bool:
    """
    Return whether a stored title carries the legacy closed marker.

    :param title: Stored conversation title, e.g.
        ``"researcher:auth:closed:conv_abc123"``.
    :returns: ``True`` when the title contains
        :data:`CLOSED_TITLE_INFIX`.
    """
    return bool(title and CLOSED_TITLE_INFIX in title)


def labels_with_closed_status(
    labels: Mapping[str, str] | None,
    title: str | None,
) -> dict[str, str]:
    """
    Return labels augmented with the derived closed-state marker.

    New closes persist ``omnigent.closed=true`` directly. Older
    rows only have the title suffix, so API responses synthesize the
    same label for clients and write guards.

    :param labels: Persisted session labels, e.g.
        ``{"omnigent.wrapper": "codex-native-ui"}``.
    :param title: Stored conversation title, e.g.
        ``"researcher:auth:closed:conv_abc123"``.
    :returns: A mutable labels dict with ``omnigent.closed=true``
        added when the title marker is present.
    """
    result = dict(labels or {})
    if has_closed_title_marker(title):
        result[CLOSED_LABEL_KEY] = CLOSED_LABEL_VALUE
    return result


def is_session_closed(
    labels: Mapping[str, str] | None,
    title: str | None = None,
) -> bool:
    """
    Return whether a session is closed to new user input.

    :param labels: Session labels, e.g.
        ``{"omnigent.closed": "true"}``.
    :param title: Optional stored title for legacy closed rows, e.g.
        ``"researcher:auth:closed:conv_abc123"``.
    :returns: ``True`` when the explicit label is set or the legacy
        title marker is present.
    """
    return (labels or {}).get(CLOSED_LABEL_KEY) == CLOSED_LABEL_VALUE or has_closed_title_marker(
        title
    )


# Label stamped on a child archived by its parent via ``sys_session_archive``.
# Informational only: any client with edit access can PATCH labels, so the
# authoritative record is the audit log (:func:`audit_session_archive`).
ARCHIVED_BY_PARENT_LABEL_KEY = "omnigent.archived_by_parent"

_SESSION_ARCHIVE_AUDIT_LOGGER = logging.getLogger("omnigent.audit.session_archive")


def audit_session_archive(
    *, caller_id: str, target_id: str, outcome: str, reason: str | None = None
) -> None:
    """
    Emit one structured audit record for a ``sys_session_archive`` attempt.

    Shared by the runner REST path and the in-process tool so both audit
    identically. Logged at INFO on ``omnigent.audit.session_archive`` for
    every attempt (success and refusal) with ids and the outcome only; no
    titles or message bodies. Values are ``repr``-quoted so a model-supplied
    id containing newlines cannot forge extra audit lines.

    :param caller_id: The archiving parent session id, e.g. ``"conv_hub"``.
    :param target_id: The requested child session id (model-supplied).
    :param outcome: ``"archived"``, ``"already_archived"``, or the error
        code returned to the LLM, e.g. ``"session_running"``.
    :param reason: Optional refusal detail, e.g. ``"status=running"``.
    """
    _SESSION_ARCHIVE_AUDIT_LOGGER.info(
        "sys_session_archive caller=%r target=%r outcome=%r reason=%r",
        caller_id,
        target_id,
        outcome,
        reason,
    )
