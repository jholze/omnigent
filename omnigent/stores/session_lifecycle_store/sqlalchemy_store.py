"""SQLAlchemy-backed session-lifecycle outbox and elicitation ledger."""

from __future__ import annotations

from sqlalchemy import desc, exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from omnigent.db.db_models import (
    SqlSessionElicitation,
    SqlSessionLifecycleCursor,
    SqlSessionLifecycleOutbox,
    current_workspace_id,
)
from omnigent.db.enum_codecs import (
    decode_session_elicitation_status,
    decode_session_lifecycle_event_type,
    decode_session_lifecycle_outbox_status,
    encode_session_elicitation_status,
    encode_session_lifecycle_event_type,
    encode_session_lifecycle_outbox_status,
)
from omnigent.db.utils import get_or_create_engine, make_named_managed_session_maker
from omnigent.entities import LifecycleOutboxEvent, SessionElicitation
from omnigent.stores.session_lifecycle_store import SessionLifecycleStore

_LEASE_BLOCKING_STATUSES = (
    encode_session_lifecycle_outbox_status("pending"),
    encode_session_lifecycle_outbox_status("leased"),
    # dead_letter keeps retrying, so a later sequence must not skip it.
    encode_session_lifecycle_outbox_status("dead_letter"),
)
_CLAIMABLE_STATUSES = (
    encode_session_lifecycle_outbox_status("pending"),
    encode_session_lifecycle_outbox_status("dead_letter"),
)


def _outbox_to_entity(row: SqlSessionLifecycleOutbox) -> LifecycleOutboxEvent:
    return LifecycleOutboxEvent(
        id=row.id,
        session_id=row.session_id,
        event_type=decode_session_lifecycle_event_type(row.event_type),
        transition_key=row.transition_key,
        sequence=row.sequence,
        event_version=row.event_version,
        payload=row.payload,
        status=decode_session_lifecycle_outbox_status(row.status),
        attempt_count=row.attempt_count,
        next_attempt_at=row.next_attempt_at,
        created_at=row.created_at,
        workspace_id=row.workspace_id,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        last_attempt_at=row.last_attempt_at,
        delivered_at=row.delivered_at,
        last_http_status=row.last_http_status,
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
    )


def _elicitation_to_entity(row: SqlSessionElicitation) -> SessionElicitation:
    return SessionElicitation(
        id=row.id,
        session_id=row.session_id,
        status=decode_session_elicitation_status(row.status),
        request_payload=row.request_payload,
        created_at=row.created_at,
        workspace_id=row.workspace_id,
        decision_payload=row.decision_payload,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        resolved_at=row.resolved_at,
    )


class SqlAlchemySessionLifecycleStore(SessionLifecycleStore):
    """SQLAlchemy implementation of :class:`SessionLifecycleStore`."""

    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.session_lifecycle_store",
            immediate=True,
        )
        self._supports_for_update = self._engine.dialect.name != "sqlite"

    def _allocate_sequence(self, session: Session, session_id: str) -> int:
        workspace_id = current_workspace_id()
        stmt = select(SqlSessionLifecycleCursor).where(
            SqlSessionLifecycleCursor.workspace_id == workspace_id,
            SqlSessionLifecycleCursor.session_id == session_id,
        )
        if self._supports_for_update:
            stmt = stmt.with_for_update()
        cursor = session.execute(stmt).scalar_one_or_none()
        if cursor is None:
            cursor = SqlSessionLifecycleCursor(
                workspace_id=workspace_id,
                session_id=session_id,
                next_sequence=1,
            )
            try:
                with session.begin_nested():
                    session.add(cursor)
                    session.flush()
            except IntegrityError:
                stmt = select(SqlSessionLifecycleCursor).where(
                    SqlSessionLifecycleCursor.workspace_id == workspace_id,
                    SqlSessionLifecycleCursor.session_id == session_id,
                )
                if self._supports_for_update:
                    stmt = stmt.with_for_update()
                cursor = session.execute(stmt).scalar_one()
        allocated = cursor.next_sequence
        cursor.next_sequence = allocated + 1
        session.flush()
        return allocated

    def _get_by_transition(
        self,
        session: Session,
        session_id: str,
        event_type: str,
        transition_key: str,
    ) -> SqlSessionLifecycleOutbox | None:
        stmt = select(SqlSessionLifecycleOutbox).where(
            SqlSessionLifecycleOutbox.workspace_id == current_workspace_id(),
            SqlSessionLifecycleOutbox.session_id == session_id,
            SqlSessionLifecycleOutbox.event_type
            == encode_session_lifecycle_event_type(event_type),
            SqlSessionLifecycleOutbox.transition_key == transition_key,
        )
        return session.execute(stmt).scalar_one_or_none()

    def _new_outbox_row(
        self,
        *,
        session: Session,
        event_id: str,
        session_id: str,
        event_type: str,
        transition_key: str,
        payload: str,
        now: int,
    ) -> SqlSessionLifecycleOutbox:
        return SqlSessionLifecycleOutbox(
            id=event_id,
            session_id=session_id,
            event_type=encode_session_lifecycle_event_type(event_type),
            transition_key=transition_key,
            sequence=self._allocate_sequence(session, session_id),
            event_version=1,
            payload=payload,
            status=encode_session_lifecycle_outbox_status("pending"),
            attempt_count=0,
            next_attempt_at=now,
            created_at=now,
        )

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
        with self._session("record_lifecycle_event") as session:
            existing = self._get_by_transition(session, session_id, event_type, transition_key)
            if existing is not None:
                return _outbox_to_entity(existing), False
            row = self._new_outbox_row(
                session=session,
                event_id=event_id,
                session_id=session_id,
                event_type=event_type,
                transition_key=transition_key,
                payload=payload,
                now=now,
            )
            try:
                with session.begin_nested():
                    session.add(row)
                    session.flush()
            except IntegrityError:
                existing = self._get_by_transition(session, session_id, event_type, transition_key)
                assert existing is not None
                return _outbox_to_entity(existing), False
            return _outbox_to_entity(row), True

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
        with self._session("record_elicitation_raised") as session:
            existing = session.get(SqlSessionElicitation, (current_workspace_id(), elicitation_id))
            if existing is not None:
                return _elicitation_to_entity(existing), None, False
            elicitation = SqlSessionElicitation(
                id=elicitation_id,
                session_id=session_id,
                status=encode_session_elicitation_status("pending"),
                request_payload=request_payload,
                created_at=now,
            )
            write_outbox = (
                outbox_event_id is not None
                and transition_key is not None
                and outbox_payload is not None
            )
            outbox_row: SqlSessionLifecycleOutbox | None = None
            if write_outbox:
                assert outbox_event_id is not None
                assert transition_key is not None
                assert outbox_payload is not None
                outbox_row = self._new_outbox_row(
                    session=session,
                    event_id=outbox_event_id,
                    session_id=session_id,
                    event_type=outbox_event_type,
                    transition_key=transition_key,
                    payload=outbox_payload,
                    now=now,
                )
            try:
                with session.begin_nested():
                    session.add(elicitation)
                    if outbox_row is not None:
                        session.add(outbox_row)
                    session.flush()
            except IntegrityError:
                existing = session.get(
                    SqlSessionElicitation, (current_workspace_id(), elicitation_id)
                )
                assert existing is not None
                existing_outbox = None
                if write_outbox:
                    assert transition_key is not None
                    existing_row = self._get_by_transition(
                        session, session_id, outbox_event_type, transition_key
                    )
                    existing_outbox = (
                        _outbox_to_entity(existing_row) if existing_row is not None else None
                    )
                return _elicitation_to_entity(existing), existing_outbox, False
            return (
                _elicitation_to_entity(elicitation),
                _outbox_to_entity(outbox_row) if outbox_row is not None else None,
                True,
            )

    def get_elicitation(self, elicitation_id: str) -> SessionElicitation | None:
        with self._session("get_elicitation") as session:
            row = session.get(SqlSessionElicitation, (current_workspace_id(), elicitation_id))
            return _elicitation_to_entity(row) if row is not None else None

    def list_decided_undelivered(self, session_id: str) -> list[SessionElicitation]:
        decided_code = encode_session_elicitation_status("decided")
        with self._session("list_decided_undelivered") as session:
            stmt = (
                select(SqlSessionElicitation)
                .where(
                    SqlSessionElicitation.workspace_id == current_workspace_id(),
                    SqlSessionElicitation.session_id == session_id,
                    SqlSessionElicitation.status == decided_code,
                )
                .order_by(SqlSessionElicitation.decided_at.asc())
            )
            rows = session.execute(stmt).scalars().all()
            return [_elicitation_to_entity(row) for row in rows]

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
        pending_code = encode_session_elicitation_status("pending")
        with self._session("record_elicitation_decision") as session:
            row = session.get(
                SqlSessionElicitation,
                (current_workspace_id(), elicitation_id),
                with_for_update=self._supports_for_update,
            )
            if row is None:
                return None
            if row.status == pending_code:
                row.status = encode_session_elicitation_status("decided")
                row.decision_payload = decision_payload
                row.decided_by = decided_by
                row.decided_at = now
                if (
                    outbox_event_id is not None
                    and outbox_transition_key is not None
                    and outbox_payload is not None
                ):
                    outbox_row = self._new_outbox_row(
                        session=session,
                        event_id=outbox_event_id,
                        session_id=row.session_id,
                        event_type=outbox_event_type,
                        transition_key=outbox_transition_key,
                        payload=outbox_payload,
                        now=now,
                    )
                    try:
                        with session.begin_nested():
                            session.add(outbox_row)
                            session.flush()
                    except IntegrityError:
                        pass
                session.flush()
            return _elicitation_to_entity(row)

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
        decided_code = encode_session_elicitation_status("decided")
        with self._session("record_elicitation_consumed") as session:
            row = session.get(
                SqlSessionElicitation,
                (current_workspace_id(), elicitation_id),
                with_for_update=self._supports_for_update,
            )
            if row is None:
                return None, None, False
            if row.status != decided_code:
                existing_outbox = self._get_by_transition(
                    session, row.session_id, outbox_event_type, transition_key
                )
                return (
                    _elicitation_to_entity(row),
                    _outbox_to_entity(existing_outbox) if existing_outbox is not None else None,
                    False,
                )
            row.status = encode_session_elicitation_status("delivered_to_runner")
            row.resolved_at = resolved_at
            outbox_row = self._new_outbox_row(
                session=session,
                event_id=outbox_event_id,
                session_id=row.session_id,
                event_type=outbox_event_type,
                transition_key=transition_key,
                payload=outbox_payload,
                now=resolved_at,
            )
            try:
                with session.begin_nested():
                    session.add(outbox_row)
                    session.flush()
            except IntegrityError:
                existing_outbox = self._get_by_transition(
                    session, row.session_id, outbox_event_type, transition_key
                )
                return (
                    _elicitation_to_entity(row),
                    _outbox_to_entity(existing_outbox) if existing_outbox is not None else None,
                    False,
                )
            return _elicitation_to_entity(row), _outbox_to_entity(outbox_row), True

    def claim_batch(
        self,
        *,
        limit: int,
        now: int,
        lease_owner: str,
        lease_seconds: int,
    ) -> list[LifecycleOutboxEvent]:
        blocker = aliased(SqlSessionLifecycleOutbox)
        blocked = (
            select(1)
            .where(
                blocker.workspace_id == SqlSessionLifecycleOutbox.workspace_id,
                blocker.session_id == SqlSessionLifecycleOutbox.session_id,
                blocker.sequence < SqlSessionLifecycleOutbox.sequence,
                blocker.status.in_(_LEASE_BLOCKING_STATUSES),
            )
            .correlate(SqlSessionLifecycleOutbox)
        )
        stmt = (
            select(SqlSessionLifecycleOutbox)
            .where(
                SqlSessionLifecycleOutbox.workspace_id == current_workspace_id(),
                SqlSessionLifecycleOutbox.status.in_(_CLAIMABLE_STATUSES),
                SqlSessionLifecycleOutbox.next_attempt_at <= now,
                ~exists(blocked),
            )
            .order_by(SqlSessionLifecycleOutbox.next_attempt_at.asc())
            .limit(limit)
        )
        if self._supports_for_update:
            stmt = stmt.with_for_update(skip_locked=True)
        with self._session("claim_lifecycle_batch") as session:
            rows = session.execute(stmt).scalars().all()
            claimed: list[LifecycleOutboxEvent] = []
            for row in rows:
                row.status = encode_session_lifecycle_outbox_status("leased")
                row.attempt_count += 1
                row.last_attempt_at = now
                row.lease_owner = lease_owner
                row.lease_expires_at = now + lease_seconds
                claimed.append(_outbox_to_entity(row))
            session.flush()
            return claimed

    def mark_delivered(
        self,
        event_id: str,
        *,
        workspace_id: int,
        delivered_at: int,
        http_status: int,
        lease_owner: str | None = None,
    ) -> None:
        leased_code = encode_session_lifecycle_outbox_status("leased")
        with self._session("mark_lifecycle_delivered") as session:
            row = session.get(SqlSessionLifecycleOutbox, (workspace_id, event_id))
            if row is None or row.status != leased_code:
                return
            if lease_owner is not None and row.lease_owner != lease_owner:
                return
            row.status = encode_session_lifecycle_outbox_status("delivered")
            row.delivered_at = delivered_at
            row.last_http_status = http_status
            row.lease_owner = None
            row.lease_expires_at = None

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
        leased_code = encode_session_lifecycle_outbox_status("leased")
        with self._session("mark_lifecycle_delivery_failed") as session:
            row = session.get(SqlSessionLifecycleOutbox, (workspace_id, event_id))
            if row is None or row.status != leased_code:
                return
            if lease_owner is not None and row.lease_owner != lease_owner:
                return
            dead = row.attempt_count >= dead_letter_after_attempts
            row.status = encode_session_lifecycle_outbox_status(
                "dead_letter" if dead else "pending"
            )
            row.next_attempt_at = next_attempt_at
            row.last_http_status = http_status
            row.last_error_code = error_code
            row.last_error_message = error_message[:256] if error_message else None
            row.lease_owner = None
            row.lease_expires_at = None

    def reclaim_expired_leases(self, *, now: int) -> int:
        leased_code = encode_session_lifecycle_outbox_status("leased")
        pending_code = encode_session_lifecycle_outbox_status("pending")
        with self._session("reclaim_expired_lifecycle_leases") as session:
            stmt = select(SqlSessionLifecycleOutbox).where(
                SqlSessionLifecycleOutbox.status == leased_code,
                SqlSessionLifecycleOutbox.lease_expires_at.is_not(None),
                SqlSessionLifecycleOutbox.lease_expires_at < now,
            )
            rows = session.execute(stmt).scalars().all()
            for row in rows:
                row.status = pending_code
                row.lease_owner = None
                row.lease_expires_at = None
                row.next_attempt_at = now
            session.flush()
            return len(rows)

    def list_deliveries(
        self,
        session_id: str,
        *,
        limit: int = 100,
        after_id: str | None = None,
    ) -> tuple[list[LifecycleOutboxEvent], str | None]:
        with self._session("list_lifecycle_deliveries") as session:
            stmt = (
                select(SqlSessionLifecycleOutbox)
                .where(
                    SqlSessionLifecycleOutbox.workspace_id == current_workspace_id(),
                    SqlSessionLifecycleOutbox.session_id == session_id,
                )
                .order_by(desc(SqlSessionLifecycleOutbox.sequence))
            )
            if after_id is not None:
                cursor_seq = (
                    select(SqlSessionLifecycleOutbox.sequence)
                    .where(
                        SqlSessionLifecycleOutbox.workspace_id == current_workspace_id(),
                        SqlSessionLifecycleOutbox.id == after_id,
                    )
                    .scalar_subquery()
                )
                stmt = stmt.where(SqlSessionLifecycleOutbox.sequence < cursor_seq)
            rows = session.execute(stmt.limit(limit + 1)).scalars().all()
            next_cursor = rows[limit - 1].id if len(rows) > limit else None
            page = rows[:limit]
            return [_outbox_to_entity(row) for row in page], next_cursor

    def latest_delivery(self, session_id: str) -> LifecycleOutboxEvent | None:
        with self._session("latest_lifecycle_delivery") as session:
            stmt = (
                select(SqlSessionLifecycleOutbox)
                .where(
                    SqlSessionLifecycleOutbox.workspace_id == current_workspace_id(),
                    SqlSessionLifecycleOutbox.session_id == session_id,
                )
                .order_by(desc(SqlSessionLifecycleOutbox.sequence))
                .limit(1)
            )
            row = session.execute(stmt).scalars().first()
            return _outbox_to_entity(row) if row is not None else None

    def list_by_status(self, status: str, *, limit: int = 100) -> list[LifecycleOutboxEvent]:
        code = encode_session_lifecycle_outbox_status(status)
        with self._session("list_lifecycle_by_status") as session:
            stmt = (
                select(SqlSessionLifecycleOutbox)
                .where(
                    SqlSessionLifecycleOutbox.workspace_id == current_workspace_id(),
                    SqlSessionLifecycleOutbox.status == code,
                )
                .order_by(desc(SqlSessionLifecycleOutbox.created_at))
                .limit(limit)
            )
            rows = session.execute(stmt).scalars().all()
            return [_outbox_to_entity(row) for row in rows]

    def requeue_dead_letters(self, event_ids: list[str] | None, *, now: int) -> int:
        dead_code = encode_session_lifecycle_outbox_status("dead_letter")
        pending_code = encode_session_lifecycle_outbox_status("pending")
        with self._session("requeue_dead_lettered_deliveries") as session:
            stmt = select(SqlSessionLifecycleOutbox).where(
                SqlSessionLifecycleOutbox.workspace_id == current_workspace_id(),
                SqlSessionLifecycleOutbox.status == dead_code,
            )
            if event_ids is not None:
                stmt = stmt.where(SqlSessionLifecycleOutbox.id.in_(event_ids))
            rows = session.execute(stmt).scalars().all()
            for row in rows:
                row.status = pending_code
                row.attempt_count = 0
                row.next_attempt_at = now
                row.lease_owner = None
                row.lease_expires_at = None
                row.last_http_status = None
                row.last_error_code = None
                row.last_error_message = None
            session.flush()
            return len(rows)
