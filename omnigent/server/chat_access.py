"""Who may read or post in a team-chat channel.

Membership is the R0 rule: a public channel is readable and writable by
its members only, and a DM is readable and writable by its participants
only. A non-member of a public channel is refused with 403
``not_a_member``; a non-participant of a DM is refused with 404 so the
channel's existence is not leaked (FR-016, FR-022).

The store is the only source of membership; these helpers map its rows
onto the HTTP outcome the routes raise. An agent account may read or
post in a channel or DM only where its owner is also a member. On a
session chat the account has the same access as its owner.
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


def author_principal_type(author_kind: str) -> str:
    """Map a stored author kind onto the MCP principal type.

    :returns: ``human``, ``agent_account``, ``omnigent_agent``, or ``system``.
    """
    if author_kind == "system":
        return "system"
    if author_kind in {"agent", "omnigent_agent"}:
        return "omnigent_agent"
    if author_kind == "agent_account":
        return "agent_account"
    return "human"


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


def _session_owner_allows(store: ChatStore, channel: ChatChannel, principal_id: str) -> bool:
    """Whether an agent account may use a session chat its owner can use."""
    if channel.kind != "session" or principal_kind(principal_id) != "agent_account":
        return False
    account = store.get_agent_account_by_principal(principal_id)
    if account is None:
        return False
    return store.is_member(channel.id, account.owner_id)


def require_read(store: ChatStore, channel: ChatChannel, principal_id: str) -> None:
    """Raise when *principal_id* may not read *channel*.

    :raises OmnigentError: 403 ``not_a_member`` for a public channel, or
        404 for a DM or session chat, when the caller is not a member. An
        agent account whose owner is not a member is refused the same way.
        On a session chat the account follows its owner's membership.
    """
    if _session_owner_allows(store, channel, principal_id):
        return
    if can_read(store, channel, principal_id) and _owner_allows(store, channel, principal_id):
        return
    if channel.kind == "channel":
        raise _not_a_member()
    raise _not_found()


def require_post(store: ChatStore, channel: ChatChannel, principal_id: str) -> None:
    """Raise when *principal_id* may not post in *channel*.

    :raises OmnigentError: 403 ``not_a_member`` for a public channel, or
        404 for a DM or session chat, when the caller is not a member. An
        agent account whose owner is not a member is refused the same way.
        On a session chat the account follows its owner's membership.
    """
    if _session_owner_allows(store, channel, principal_id):
        return
    if can_post(store, channel, principal_id) and _owner_allows(store, channel, principal_id):
        return
    if channel.kind == "channel":
        raise _not_a_member()
    raise _not_found()


def require_owner_membership(store: ChatStore, channel: ChatChannel, principal_id: str) -> None:
    """Raise when an agent account's owner is not a member of *channel*.

    Human callers pass. A missing or revoked agent account is refused.
    """
    if _owner_allows(store, channel, principal_id):
        return
    if channel.kind == "channel":
        raise _not_a_member()
    raise _not_found()


def _owner_allows(store: ChatStore, channel: ChatChannel, principal_id: str) -> bool:
    """Whether *principal_id* stays inside its owner's membership.

    Human principals have no owner constraint. An agent account must still
    have a live row, and that row's owner must be a member of *channel*.
    """
    if principal_kind(principal_id) != "agent_account":
        return True
    account = store.get_agent_account_by_principal(principal_id)
    if account is None:
        return False
    return store.is_member(channel.id, account.owner_id)


def _not_a_member() -> OmnigentError:
    """Build the 403 ``not_a_member`` rejection."""
    return OmnigentError("Not a member of this channel", code=ErrorCode.NOT_A_MEMBER)


def _not_found() -> OmnigentError:
    """Build the 404 used to hide a DM from non-participants."""
    return OmnigentError("Channel not found", code=ErrorCode.NOT_FOUND)
