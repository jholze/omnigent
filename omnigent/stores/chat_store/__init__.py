"""Team-chat persistence: channels, DMs, messages, threads and mentions.

The store is the persistence layer behind the chat HTTP routes. It speaks
typed results, not HTTP: a caller maps each result status to a status code
and JSON error code. Times are epoch microseconds, ids are bare 32-char hex,
and there are no foreign keys (application-owned references).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class ChatChannel:
    """A channel, direct message, or session-chat container.

    :param id: Bare 32-char hex uuid.
    :param kind: ``"channel"``, ``"dm"`` or ``"session"``.
    :param name: Channel name; ``None`` for DMs and session chats.
    :param dm_key: sha256 of the sorted DM principals; only for DMs.
    """

    id: str
    kind: str
    name: str | None
    topic: str | None
    project_name: str | None
    visibility: str
    dm_key: str | None
    session_root_id: str | None
    created_by: str | None
    created_at: int
    archived_at: int | None


@dataclass(frozen=True)
class ChatMember:
    """Membership of one principal in one channel."""

    channel_id: str
    principal_id: str
    role: str
    joined_at: int


@dataclass(frozen=True)
class ChatMessage:
    """One message (top-level or thread reply) in a channel."""

    id: str
    channel_id: str
    thread_root_id: str | None
    author_id: str
    author_kind: str
    created_at: int
    body: str
    client_id: str | None


@dataclass(frozen=True)
class ChatThread:
    """Thread state for a top-level message.

    :param participants: Latest repliers, newest first, at most five.
    """

    root_message_id: str
    channel_id: str
    reply_count: int
    last_reply_at: int | None
    participants: tuple[str, ...]


class PostStatus(StrEnum):
    """Outcome of storing one message."""

    CREATED = "created"
    REPLAYED = "replayed"
    BODY_CONFLICT = "body_conflict"
    NESTED_THREAD = "nested_thread"
    THREAD_ROOT_NOT_FOUND = "thread_root_not_found"


@dataclass(frozen=True)
class PostMessageResult:
    """Typed result of a message store attempt.

    :param status: The outcome.
    :param message: The stored (or replayed) message, or ``None`` on error.
    """

    status: PostStatus
    message: ChatMessage | None = None


class DmStatus(StrEnum):
    """Outcome of opening a direct message."""

    CREATED = "created"
    EXISTING = "existing"
    AGENT_OWNER_REQUIRED = "agent_owner_required"


@dataclass(frozen=True)
class DmResult:
    """Typed result of a DM open attempt.

    :param channel: The DM, or ``None`` when the result is an error.
    """

    status: DmStatus
    channel: ChatChannel | None = None


@dataclass(frozen=True)
class MessagePage:
    """One ascending page of messages with ``(created_at, id)`` cursors."""

    items: tuple[ChatMessage, ...]
    has_older: bool
    has_more: bool
    next_after: tuple[int, str] | None
    next_before: tuple[int, str] | None


class ChatStore(ABC):
    """Persistence for team chat.

    Concrete stores are opened against one database URI, mirroring the
    other ``omnigent/stores/*`` backends.
    """

    def __init__(self, storage_location: str) -> None:
        """Open the store against one backend URI.

        :param storage_location: SQLAlchemy URI, e.g. ``"sqlite:///chat.db"``.
        """
        self.storage_location = storage_location

    @abstractmethod
    def bootstrap_channel(self, name: str, created_by: str | None = None) -> ChatChannel:
        """Ensure a channel named *name* exists and add *created_by*.

        Idempotent: a repeated or concurrent call returns the one existing
        channel instead of failing on the unique ``(workspace_id, name)``
        index. *created_by* is added as a member when present.

        :returns: The existing or newly created channel.
        """
        ...

    @abstractmethod
    def get_channel(self, channel_id: str) -> ChatChannel | None:
        """Return a channel by id, or ``None`` when it does not exist."""
        ...

    @abstractmethod
    def add_member(self, channel_id: str, principal_id: str, role: str = "member") -> ChatMember:
        """Add *principal_id* to *channel_id*, keeping an existing row.

        :returns: The current membership row.
        """
        ...

    @abstractmethod
    def get_or_create_dm(self, principals: list[str]) -> DmResult:
        """Return the DM for exactly *principals*, creating it if needed.

        The participant set is order-independent. An agent account is only
        allowed in the set when its owner is also a participant, otherwise
        the result is ``agent_owner_required`` and nothing is stored.
        """
        ...

    @abstractmethod
    def post_message(
        self,
        channel_id: str,
        author_id: str,
        body: str,
        *,
        author_kind: str = "user",
        client_id: str | None = None,
        thread_root_id: str | None = None,
        omnigent_agent_handles: Collection[str] | None = None,
    ) -> PostMessageResult:
        """Store one message, or report why nothing was stored.

        A repeated ``(author, channel, client_id)`` with the same body
        returns the original message (``replayed``); a different body is
        ``body_conflict``. A ``thread_root_id`` must name a top-level
        message of the same channel; otherwise ``nested_thread`` or
        ``thread_root_not_found`` is returned and nothing is stored.

        :param omnigent_agent_handles: Agent handles the caller can mention;
            ``<@omnigent_agent:…>`` tokens outside this set stay literal.
        """
        ...

    @abstractmethod
    def list_messages(
        self,
        channel_id: str,
        *,
        thread_root_id: str | None = None,
        limit: int = 50,
        tail: int | None = None,
        after: tuple[int, str] | None = None,
        before: tuple[int, str] | None = None,
    ) -> MessagePage:
        """List messages in ascending ``(created_at, id)`` order.

        :param thread_root_id: ``None`` selects top-level messages; a value
            selects the replies of that thread (the root itself excluded).
        :param tail: Return the newest *tail* messages, ascending.
        :param after: Exclusive forward cursor ``(created_at, id)``.
        :param before: Exclusive backward cursor ``(created_at, id)``.
        """
        ...

    @abstractmethod
    def get_thread(self, root_message_id: str) -> ChatThread | None:
        """Return the thread state for a top-level message, or ``None``."""
        ...
