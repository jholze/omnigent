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


@dataclass(frozen=True)
class ChatAgentAccount:
    """An agent account surfaced in the chat directory.

    :param principal_id: The account's principal id (``agent:<uuid>``).
    :param owner_id: The owning human principal.
    :param token_prefix: Eight-character display hint. Never the raw token.
    """

    id: str
    principal_id: str
    owner_id: str
    display_name: str
    handle: str
    token_prefix: str = ""
    secret_version: int = 1
    last_seen_at: int | None = None
    revoked_at: int | None = None


@dataclass(frozen=True)
class AgentTokenIssue:
    """A raw ``ogc_`` token returned once, plus the stored account.

    The raw token is not persisted. Callers show it to the owner and drop it.
    """

    account: ChatAgentAccount
    token: str


class PostStatus(StrEnum):
    """Outcome of storing one message."""

    CREATED = "created"
    REPLAYED = "replayed"
    BODY_CONFLICT = "body_conflict"
    NESTED_THREAD = "nested_thread"
    THREAD_ROOT_NOT_FOUND = "thread_root_not_found"
    BOT_STREAK_LIMIT = "bot_streak_limit"


@dataclass(frozen=True)
class PostMessageResult:
    """Typed result of a message store attempt.

    :param status: The outcome.
    :param message: The stored (or replayed) message, or ``None`` on error.
    """

    status: PostStatus
    message: ChatMessage | None = None
    notice: ChatMessage | None = None


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


class AccountStatus(StrEnum):
    """Outcome of creating, rotating, or revoking an agent account."""

    CREATED = "created"
    ROTATED = "rotated"
    REVOKED = "revoked"
    HANDLE_TAKEN = "handle_taken"
    NOT_FOUND = "not_found"
    REVOKED_ACCOUNT = "revoked_account"
    LIMIT = "limit"


@dataclass(frozen=True)
class AccountWriteResult:
    """Typed result of an agent-account write.

    :param issue: Present when a new raw token was minted.
    """

    status: AccountStatus
    issue: AgentTokenIssue | None = None


class ReadStatus(StrEnum):
    """Outcome of advancing a read cursor."""

    ADVANCED = "advanced"
    UNCHANGED = "unchanged"
    NOT_FOUND = "not_found"
    WRONG_SCOPE = "wrong_scope"


@dataclass(frozen=True)
class ReadCursor:
    """The stored cursor after a mark-read attempt."""

    channel_id: str
    principal_id: str
    thread_root_id: str
    last_read_at: int | None
    last_read_id: str | None


@dataclass(frozen=True)
class MarkReadResult:
    """Typed result of a mark-read attempt."""

    status: ReadStatus
    cursor: ReadCursor | None = None


@dataclass(frozen=True)
class UnreadMessage:
    """One unread message and why it woke the caller.

    :param reason: ``mention``, ``dm``, or ``thread_reply``.
    """

    message: ChatMessage
    reason: str


@dataclass(frozen=True)
class ChangesPage:
    """One ascending page of the caller's change feed.

    :param items: Messages in channels the caller can read, ordered by
        ``(created_at, id)`` ascending.
    :param next_after: The ``(created_at, id)`` cursor of the last item,
        or ``None`` when the page is empty.
    :param reset: ``True`` when more than the page limit is pending, so
        the caller should reload bootstrap instead of paging further.
    """

    items: tuple[ChatMessage, ...]
    next_after: tuple[int, str] | None
    reset: bool


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
    def get_channel_by_name(self, name: str) -> ChatChannel | None:
        """Return the public channel named *name*, or ``None``.

        Only ``kind="channel"`` rows carry a name, so this never matches
        a DM or session chat.
        """
        ...

    @abstractmethod
    def is_member(self, channel_id: str, principal_id: str) -> bool:
        """Whether *principal_id* is a member of *channel_id*."""
        ...

    @abstractmethod
    def list_joined_channels(self, principal_id: str) -> tuple[ChatChannel, ...]:
        """Public channels *principal_id* is a member of, oldest first."""
        ...

    @abstractmethod
    def list_joined_dms(self, principal_id: str) -> tuple[ChatChannel, ...]:
        """Direct messages *principal_id* is a participant of, oldest first."""
        ...

    @abstractmethod
    def list_members(self, channel_id: str) -> tuple[ChatMember, ...]:
        """Members of *channel_id*, ordered by join time."""
        ...

    @abstractmethod
    def remove_member(self, channel_id: str, principal_id: str) -> bool:
        """Drop *principal_id* from *channel_id*.

        :returns: ``True`` when a row was removed, ``False`` when the
            principal was not a member.
        """
        ...

    @abstractmethod
    def list_active_agent_accounts(self) -> tuple[ChatAgentAccount, ...]:
        """Return the workspace's non-revoked agent accounts, oldest first."""
        ...

    @abstractmethod
    def list_changes(
        self,
        principal_id: str,
        *,
        after: tuple[int, str] | None = None,
        limit: int = 1000,
    ) -> ChangesPage:
        """Return messages created after *after* in channels the caller reads.

        Only messages in channels where *principal_id* is a member are
        included, in ascending ``(created_at, id)`` order. When more than
        *limit* rows are pending the result sets :attr:`ChangesPage.reset`.
        """
        ...

    @abstractmethod
    def get_thread(self, root_message_id: str) -> ChatThread | None:
        """Return the thread state for a top-level message, or ``None``."""
        ...

    @abstractmethod
    def list_thread_agent_states(self, root_message_ids: Collection[str]) -> dict[str, str]:
        """Return ``agent_state`` for each thread root id that has a row.

        Ids without a thread row are omitted from the result, so a caller
        can distinguish "no thread yet" from a stored state. Read-only;
        used to annotate thread-root messages on the channel view without
        exposing the thread's ``session_id`` or other private columns.
        """
        ...

    @abstractmethod
    def create_agent_account(
        self,
        owner_id: str,
        display_name: str,
        handle: str,
        *,
        webhook_url: str = "",
    ) -> AccountWriteResult:
        """Create an agent account and mint its first ``ogc_`` token.

        The raw token is on :attr:`AccountWriteResult.issue` and is not stored.
        A duplicate handle in the workspace is ``handle_taken``.
        """
        ...

    @abstractmethod
    def rotate_agent_token(self, account_id: str, owner_id: str) -> AccountWriteResult:
        """Replace the stored token hash. The previous token stops authenticating.

        A wrong owner or unknown id is ``not_found``. A revoked account is
        ``revoked_account`` and keeps its old hash.
        """
        ...

    @abstractmethod
    def revoke_agent_account(self, account_id: str, owner_id: str) -> AccountWriteResult:
        """Mark the account revoked. Its current token stops authenticating."""
        ...

    @abstractmethod
    def principal_for_token_hash(self, token_hash: str) -> str | None:
        """Return the principal id for a live token hash, or ``None``.

        Revoked accounts and unknown hashes return ``None``. The lookup is by
        the global unique hash and does not require a workspace id.
        """
        ...

    @abstractmethod
    def account_for_token_hash(self, token_hash: str) -> ChatAgentAccount | None:
        """Return the live account for a token hash, or ``None``.

        The lookup is not cached. A revoked account or unknown hash is ``None``.
        """
        ...

    @abstractmethod
    def owner_account_live(self, owner_id: str) -> bool:
        """Whether *owner_id* may still act for an agent account.

        A row whose ``deleted_at`` is set is inactive in every auth mode.
        A missing row is inactive only for accounts and OIDC, where identity
        remap deletes the old user. Header and single-user servers have no
        users row, so a missing row stays live there.
        """
        ...

    @abstractmethod
    def count_sessions_started_by(self, account_id: str, since_us: int) -> int:
        """Count sessions this agent account started at or after *since_us*."""
        ...

    @abstractmethod
    def get_agent_account_by_principal(self, principal_id: str) -> ChatAgentAccount | None:
        """Return the live account for *principal_id*, or ``None`` when revoked."""
        ...

    @abstractmethod
    def touch_agent_last_seen(self, principal_id: str) -> None:
        """Stamp ``last_seen_at`` at most once per 60 seconds.

        A missing or revoked account is left unchanged.
        """
        ...

    @abstractmethod
    def mark_read(
        self,
        channel_id: str,
        principal_id: str,
        message_id: str,
        *,
        thread_root_id: str | None = None,
        created_at: int | None = None,
    ) -> MarkReadResult:
        """Advance the caller's cursor to *message_id* if that moves it forward.

        ``thread_root_id`` empty selects the channel cursor. A message older
        than the current cursor leaves the cursor unchanged. When *created_at*
        is set and does not match the message, the cursor is left unchanged.
        """
        ...

    @abstractmethod
    def channel_unread_counts(
        self, principal_id: str, channel_ids: Collection[str]
    ) -> dict[str, tuple[int, int]]:
        """Return ``(unread_count, mention_count)`` for each channel.

        Counts are top-level messages after the caller's channel cursor,
        excluding the caller's own. Mentions are those that name the caller.
        """
        ...

    @abstractmethod
    def list_unread(
        self,
        principal_id: str,
        *,
        limit: int = 50,
        channel_ids: Collection[str] | None = None,
    ) -> tuple[UnreadMessage, ...]:
        """Return unread mentions, DM messages, and thread replies, oldest first.

        Own messages are excluded. *limit* is capped at 50. *channel_ids*
        restricts the scan to that set; ``None`` uses the caller's memberships.
        """
        ...
