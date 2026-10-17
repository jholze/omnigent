"""Who may read or post in a team-chat channel.

Membership is the R0 rule: a public channel is readable and writable by
its members only, and a DM is readable and writable by its participants
only. A non-member of a public channel is refused with 403
``not_a_member``; a non-participant of a DM is refused with 404 so the
channel's existence is not leaked (FR-016, FR-022).

The store is the only source of membership; these helpers map its rows
onto the HTTP outcome the routes raise. Agent-account ownership rules
(FR-067) and session-chat grants are later work and deliberately absent
here.
"""

from __future__ import annotations

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.chat_store import ChatChannel, ChatStore

_AGENT_ACCOUNT_PREFIX = "agent:"


def principal_kind(principal_id: str | None) -> str:
    """Classify a principal id.

    :param principal_id: An identity such as ``"alice@example.com"`` or
        ``"agent:<uuid>"``, or ``None`` when auth is off.
    :returns: ``"agent_account"`` for agent principals, ``"user"`` otherwise.
    """
    if principal_id is not None and principal_id.startswith(_AGENT_ACCOUNT_PREFIX):
        return "agent_account"
    return "user"


def can_read(store: ChatStore, channel: ChatChannel, principal_id: str) -> bool:
    """Whether *principal_id* may read *channel*.

    :param store: Membership source.
    :param channel: The channel being read.
    :param principal_id: The resolved caller principal.
    """
    return store.is_member(channel.id, principal_id)


def can_post(store: ChatStore, channel: ChatChannel, principal_id: str) -> bool:
    """Whether *principal_id* may post in *channel*.

    R0 posts carry the same membership rule as reads.
    """
    return store.is_member(channel.id, principal_id)


def require_read(store: ChatStore, channel: ChatChannel, principal_id: str) -> None:
    """Raise when *principal_id* may not read *channel*.

    :raises OmnigentError: 403 ``not_a_member`` for a public channel, or
        404 for a DM, when the caller is not a member.
    """
    if can_read(store, channel, principal_id):
        return
    if channel.kind == "channel":
        raise _not_a_member()
    raise _not_found()


def require_post(store: ChatStore, channel: ChatChannel, principal_id: str) -> None:
    """Raise when *principal_id* may not post in *channel*.

    :raises OmnigentError: 403 ``not_a_member`` for a public channel, or
        404 for a DM, when the caller is not a member.
    """
    if can_post(store, channel, principal_id):
        return
    if channel.kind == "channel":
        raise _not_a_member()
    raise _not_found()


def _not_a_member() -> OmnigentError:
    """Build the 403 ``not_a_member`` rejection."""
    return OmnigentError("Not a member of this channel", code=ErrorCode.NOT_A_MEMBER)


def _not_found() -> OmnigentError:
    """Build the 404 used to hide a DM from non-participants."""
    return OmnigentError("Channel not found", code=ErrorCode.NOT_FOUND)
