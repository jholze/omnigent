"""Access rules and announcement for the session chat beside a root session.

The session chat is the ``kind="session"`` channel whose ``session_root_id``
names the root conversation. Membership is derived from the root's direct
grants (never ``chat_members``): a direct grant holder may read and post, an
admin may read but not post, and the ``__public__`` row grants nothing. A
posted line is announced as a bodyless ``session.human_chat`` event so the
old stream clients keep working.
"""

from __future__ import annotations

import logging

from omnigent.entities import Conversation
from omnigent.runtime import session_stream
from omnigent.server.auth import RESERVED_USER_PUBLIC
from omnigent.server.principals import principals
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.permission_store import PermissionStore

_logger = logging.getLogger(__name__)

AGENT_THREAD_LABEL_KEY = "omnigent.chat.v1"

_TREE_PAGE_SIZE = 100
_TREE_PAGE_CAP = 20


def _direct_grant_principals(
    root_conversation_id: str,
    permission_store: PermissionStore | None,
) -> frozenset[str]:
    """Distinct principals with a direct grant on the root, ``__public__`` excluded.

    Single-user mode (no permission store) has no grants, so the set is empty.
    """
    if permission_store is None:
        return frozenset()
    grants = permission_store.list_for_sessions([root_conversation_id]).get(
        root_conversation_id, []
    )
    return frozenset(grant.user_id for grant in grants if grant.user_id != RESERVED_USER_PUBLIC)


def may_read_session_chat(
    user_id: str | None,
    root_conversation_id: str,
    permission_store: PermissionStore | None,
) -> bool:
    """Whether this caller may read the session chat on a root session.

    Allowed: single-user mode (no permission store), an admin, or a direct
    ``session_permissions`` row on the root for one of the caller's principals.
    The ``__public__`` row never counts.
    """
    if permission_store is None:
        return True
    if not user_id:
        return False
    caller_ids = {pid for pid in principals(user_id) if pid and pid != RESERVED_USER_PUBLIC}
    for pid in caller_ids:
        if permission_store.is_admin(pid):
            return True
    return any(permission_store.get(pid, root_conversation_id) is not None for pid in caller_ids)


def may_post_session_chat(
    user_id: str | None,
    root_conversation_id: str,
    permission_store: PermissionStore | None,
) -> bool:
    """Whether this caller may post to the session chat on a root session.

    Allowed: single-user mode (no permission store), or a direct
    ``session_permissions`` row on the root for one of the caller's principals.
    Admins are read-only, so an admin flag alone does not allow posting.
    """
    if permission_store is None:
        return True
    if not user_id:
        return False
    caller_ids = {pid for pid in principals(user_id) if pid and pid != RESERVED_USER_PUBLIC}
    return any(permission_store.get(pid, root_conversation_id) is not None for pid in caller_ids)


def session_chat_is_shared(
    user_id: str | None,
    root_conversation_id: str,
    permission_store: PermissionStore | None,
) -> bool:
    """Whether some principal other than the caller has a direct grant.

    Ignores ``__public__``. A public-link visitor in the presence list does
    not make the session shared for this control.
    """
    if permission_store is None:
        return False
    caller_ids = principals(user_id) if user_id else frozenset()
    return any(
        pid not in caller_ids
        for pid in _direct_grant_principals(root_conversation_id, permission_store)
    )


def is_agent_thread(
    conversation: Conversation,
    conversation_store: ConversationStore,
) -> bool:
    """Whether the conversation's root carries the agent-thread label.

    Agent-thread sessions get no session-chat channel (AC-029c), so the
    chat routes refuse them. The label lives on the root; a child resolves
    to its root before checking.
    """
    labels = conversation.labels
    if conversation.id != conversation.root_conversation_id:
        root = conversation_store.get_conversation(conversation.root_conversation_id)
        if root is not None:
            labels = root.labels
    return AGENT_THREAD_LABEL_KEY in labels


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
    filters callers who fail :func:`may_read_session_chat`.
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
