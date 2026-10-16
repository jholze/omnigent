"""Session-lifecycle outbox and durable elicitation ledger."""

from __future__ import annotations

from abc import ABC, abstractmethod

from omnigent.entities import LifecycleOutboxEvent, SessionElicitation


class SessionLifecycleStore(ABC):
    """Persistence for the manager-webhook outbox and elicitation ledger."""

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: Backend URI, e.g. ``"sqlite:///chat.db"``.
        """
        self.storage_location = storage_location

    @abstractmethod
    def record_lifecycle_event(
        self,
        *,
        event_id: str,
        session_id: str,
        event_type: str,
        transition_key: str,
        payload: str,
        now: int,
    ) -> tuple[LifecycleOutboxEvent, bool]:
        """Insert one outbox row. Idempotent on the transition key.

        :returns: ``(row, inserted)``. ``inserted`` is false when the same
            transition was already recorded.
        """
        ...

    @abstractmethod
    def record_elicitation_raised(
        self,
        *,
        elicitation_id: str,
        session_id: str,
        request_payload: str,
        outbox_event_id: str | None = None,
        transition_key: str | None = None,
        outbox_payload: str | None = None,
        now: int,
        outbox_event_type: str = "session.awaiting_decision",
    ) -> tuple[SessionElicitation, LifecycleOutboxEvent | None, bool]:
        """Insert the ledger row and, when outbox fields are set, its
        awaiting-decision outbox row, in one transaction."""
        ...

    @abstractmethod
    def get_elicitation(self, elicitation_id: str) -> SessionElicitation | None:
        """Return a ledger row by id, or ``None``."""
        ...

    @abstractmethod
    def list_decided_undelivered(self, session_id: str) -> list[SessionElicitation]:
        """List ``decided`` rows whose runner has not consumed the verdict."""
        ...

    @abstractmethod
    def record_decision(
        self,
        elicitation_id: str,
        *,
        decision_payload: str,
        decided_by: str | None,
        now: int,
        outbox_event_id: str | None = None,
        outbox_transition_key: str | None = None,
        outbox_payload: str | None = None,
        outbox_event_type: str = "session.decision_resolved",
    ) -> SessionElicitation | None:
        """Record a verdict. The first pending→decided write wins.

        When outbox fields are set, the matching lifecycle event is inserted
        in the same transaction as the verdict.
        """
        ...

    @abstractmethod
    def record_elicitation_resolved(
        self,
        elicitation_id: str,
        *,
        outbox_event_id: str,
        transition_key: str,
        outbox_payload: str,
        resolved_at: int,
        outbox_event_type: str = "session.resumed",
    ) -> tuple[SessionElicitation | None, LifecycleOutboxEvent | None, bool]:
        """Mark the elicitation consumed and insert ``session.resumed``."""
        ...

    @abstractmethod
    def claim_batch(
        self,
        *,
        limit: int,
        now: int,
        lease_owner: str,
        lease_seconds: int,
    ) -> list[LifecycleOutboxEvent]:
        """Claim deliverable rows, preserving per-session sequence order."""
        ...

    @abstractmethod
    def mark_delivered(
        self,
        event_id: str,
        *,
        workspace_id: int,
        delivered_at: int,
        http_status: int,
        lease_owner: str | None = None,
    ) -> None:
        """Mark a leased row delivered.

        When *lease_owner* is set, a row leased by someone else is left
        unchanged so a stale worker cannot clobber a newer claim.
        """
        ...

    @abstractmethod
    def mark_delivery_failed(
        self,
        event_id: str,
        *,
        workspace_id: int,
        next_attempt_at: int,
        dead_letter_after_attempts: int,
        http_status: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        lease_owner: str | None = None,
    ) -> None:
        """Release a lease after a failed attempt. Dead-letter keeps retrying.

        When *lease_owner* is set, a row leased by someone else is left
        unchanged.
        """
        ...

    @abstractmethod
    def reclaim_expired_leases(self, *, now: int) -> int:
        """Return expired leases to ``pending``. Returns the count reclaimed."""
        ...

    @abstractmethod
    def list_deliveries(
        self,
        session_id: str,
        *,
        limit: int = 100,
        after_id: str | None = None,
    ) -> tuple[list[LifecycleOutboxEvent], str | None]:
        """List a session's outbox rows, newest sequence first."""
        ...

    @abstractmethod
    def latest_delivery(self, session_id: str) -> LifecycleOutboxEvent | None:
        """Return the highest-sequence outbox row for a session."""
        ...

    @abstractmethod
    def list_by_status(self, status: str, *, limit: int = 100) -> list[LifecycleOutboxEvent]:
        """List rows in *status* for the current workspace, newest first."""
        ...

    @abstractmethod
    def requeue_dead_letters(self, event_ids: list[str] | None, *, now: int) -> int:
        """Move dead-lettered rows back to ``pending`` so the dispatcher retries them.

        Resets the attempt count so the row gets a fresh budget. A running
        server's dispatcher claims the row on its next poll.

        :param event_ids: Specific event ids, or ``None`` for every dead-lettered
            row in the current workspace.
        :returns: Number of rows requeued.
        """
        ...
