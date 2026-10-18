"""SQLAlchemy store for human session-chat lines."""

from __future__ import annotations

import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from omnigent.db.db_models import SqlSessionChatMessage, current_workspace_id
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch_us,
    run_write_transaction,
)
from omnigent.stores.session_chat_store import SessionChatMessage, SessionChatStore


def _to_entity(row: SqlSessionChatMessage) -> SessionChatMessage:
    """Copy a row into a detached value.

    :param row: ORM row read inside an open session.
    :returns: The line as a dataclass.
    """
    return SessionChatMessage(
        id=row.id,
        root_conversation_id=row.root_conversation_id,
        created_by=row.created_by,
        created_at=row.created_at,
        body=row.body,
    )


class SqlAlchemySessionChatStore(SessionChatStore):
    """SQLAlchemy implementation of :class:`SessionChatStore`."""

    def __init__(self, storage_location: str) -> None:
        """Open the store against one database URI.

        :param storage_location: SQLAlchemy URI, e.g. ``"sqlite:///omnigent.db"``.
        """
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.session_chat_store",
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.session_chat_store",
            immediate=True,
        )

    def append(
        self,
        root_conversation_id: str,
        body: str,
        created_by: str | None,
    ) -> SessionChatMessage:
        """Persist one line. See the base class."""
        message_id = uuid.uuid4().hex
        created_at = now_epoch_us()

        def write(session: Session) -> SessionChatMessage:
            row = SqlSessionChatMessage(
                id=message_id,
                root_conversation_id=root_conversation_id,
                body=body,
                created_by=created_by,
                created_at=created_at,
            )
            session.add(row)
            return _to_entity(row)

        return run_write_transaction(
            self._session_immediate,
            "insert_session_chat_message",
            write,
        )

    def list_for_root(
        self,
        root_conversation_id: str,
        *,
        after: tuple[int, str] | None = None,
    ) -> list[SessionChatMessage]:
        """List lines on one root. See the base class."""
        with self._session("list_session_chat_messages") as session:
            stmt = (
                select(SqlSessionChatMessage)
                .where(
                    SqlSessionChatMessage.workspace_id == current_workspace_id(),
                    SqlSessionChatMessage.root_conversation_id == root_conversation_id,
                )
                .order_by(
                    SqlSessionChatMessage.created_at.asc(),
                    SqlSessionChatMessage.id.asc(),
                )
            )
            if after is not None:
                created_at, message_id = after
                stmt = stmt.where(
                    or_(
                        SqlSessionChatMessage.created_at > created_at,
                        and_(
                            SqlSessionChatMessage.created_at == created_at,
                            SqlSessionChatMessage.id > message_id,
                        ),
                    )
                )
            rows = session.execute(stmt).scalars().all()
            return [_to_entity(row) for row in rows]
