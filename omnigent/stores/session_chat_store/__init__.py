"""Human chat lines for one shared session tree."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SessionChatMessage:
    """One line people in a session sent to each other.

    :param id: Bare uuid hex, e.g. ``"a1b2c3d4e5f6..."``.
    :param root_conversation_id: Root of the session tree the line belongs to.
    :param created_by: Authenticated author, or ``None`` in single-user mode.
    :param created_at: Unix epoch microseconds.
    :param body: Trimmed text, at most 2000 characters.
    """

    id: str
    root_conversation_id: str
    created_by: str | None
    created_at: int
    body: str


class SessionChatStore(ABC):
    """Append-only lines stored on a session's root conversation."""

    def __init__(self, storage_location: str) -> None:
        """Initialize the store.

        :param storage_location: Backend URI, e.g. ``"sqlite:///chat.db"``.
        """
        self.storage_location = storage_location

    @abstractmethod
    def append(
        self,
        root_conversation_id: str,
        body: str,
        created_by: str | None,
    ) -> SessionChatMessage:
        """Persist one line on the root session.

        :param root_conversation_id: Root conversation id.
        :param body: Trimmed message text.
        :param created_by: Author id, or ``None`` when there is no human actor.
        :returns: The stored line.
        """
        ...

    @abstractmethod
    def list_for_root(
        self,
        root_conversation_id: str,
        *,
        after: tuple[int, str] | None = None,
    ) -> list[SessionChatMessage]:
        """Return lines ordered by ``(created_at, id)`` ascending.

        :param root_conversation_id: Root conversation id.
        :param after: Exclusive cursor. ``None`` returns every stored line.
        :returns: Matching lines. Two rows that share a microsecond both appear,
            ordered by id.
        """
        ...
