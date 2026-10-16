"""Who may read the human chat on a session, and how a new line is announced.

The public-link row (``__public__``) grants ``LEVEL_READ`` on the stream.
That is not enough to read or write this chat. Callers pass the resulting
boolean into the stream generator so a public viewer never receives the line.
"""

from __future__ import annotations

import logging

from omnigent.runtime import session_stream
from omnigent.server.auth import RESERVED_USER_PUBLIC
from omnigent.server.principals import principals
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.permission_store import PermissionStore

_logger = logging.getLogger(__name__)

_TREE_PAGE_SIZE = 100
_TREE_PAGE_CAP = 20


def may_read_human_chat(
    user_id: str | None,
    root_conversation_id: str,
    permission_store: PermissionStore | None,
) -> bool:
    """Whether this caller may read or write the human chat on a root session.

    Allowed: single-user mode (no permission store), an admin, or a direct
    ``session_permissions`` row on the root for one of the caller's principals.
    The ``__public__`` row never counts.

    :param user_id: Authenticated id, e.g. ``"alice@example.com"``.
    :param root_conversation_id: Root of the session tree.
    :param permission_store: Grant store, or ``None`` in single-user mode.
    :returns: ``True`` when the chat routes and the live event are allowed.
    """
    if permission_store is None:
        return True
    if not user_id:
        return False
    caller_ids = {pid for pid in principals(user_id) if pid and pid != RESERVED_USER_PUBLIC}
    for pid in caller_ids:
        if permission_store.is_admin(pid):
            return True
    return any(
        permission_store.get(pid, root_conversation_id) is not None for pid in caller_ids
    )


def human_chat_is_shared(
    user_id: str | None,
    root_conversation_id: str,
    permission_store: PermissionStore | None,
) -> bool:
    """Whether some principal other than the caller has a direct grant.

    Ignores ``__public__``. A public-link visitor in the presence list does
    not make the session shared for this control.

    :param user_id: Authenticated id, or ``None`` in single-user mode.
    :param root_conversation_id: Root of the session tree.
    :param permission_store: Grant store, or ``None`` when permissions are off.
    :returns: ``True`` when another person's row is on the root.
    """
    if permission_store is None:
        return False
    caller_ids = principals(user_id) if user_id else frozenset()
    grants = permission_store.list_for_sessions([root_conversation_id]).get(
        root_conversation_id, []
    )
    for grant in grants:
        if grant.user_id == RESERVED_USER_PUBLIC:
            continue
        if grant.user_id in caller_ids:
            continue
        return True
    return False


def publish_human_chat(
    conversation_store: ConversationStore,
    root_conversation_id: str,
    message_id: str,
    created_at: int,
) -> None:
    """Announce a stored line to every open stream in the session tree.

    The event carries ``id`` and ``created_at`` and not the text. Each stream
    is stamped with its own ``conversation_id`` so the web client accepts it.
    A stream with no subscriber drops the event. The HTTP generator still
    filters callers who fail :func:`may_read_human_chat`.

    :param conversation_store: Store used to list the root and its children.
    :param root_conversation_id: Root the line was stored on.
    :param message_id: Stored line id.
    :param created_at: Stored line timestamp, in microseconds.
    """
    stream_ids = {root_conversation_id}
    after: str | None = None
    seen_cursors: set[str] = set()
    try:
        for _ in range(_TREE_PAGE_CAP):
            page = conversation_store.list_conversations(
                limit=_TREE_PAGE_SIZE,
                after=after,
                kind=None,
                root_conversation_id=root_conversation_id,
                include_archived=True,
            )
            for conversation in page.data:
                stream_ids.add(conversation.id)
            if not page.has_more or page.last_id is None or page.last_id in seen_cursors:
                break
            seen_cursors.add(page.last_id)
            after = page.last_id
    except Exception:  # noqa: BLE001 — a tree-list failure still announces the root stream
        _logger.warning(
            "session chat fan-out could not list the tree for %s",
            root_conversation_id,
            exc_info=True,
        )
    for stream_id in stream_ids:
        session_stream.publish(
            stream_id,
            {
                "type": "session.human_chat",
                "conversation_id": stream_id,
                "id": message_id,
                "created_at": created_at,
            },
        )
