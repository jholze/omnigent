"""SQLAlchemy team-chat store."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Collection

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from omnigent.db.db_models import (
    SqlChatAgentAccount,
    SqlChatChannel,
    SqlChatMember,
    SqlChatMention,
    SqlChatMessage,
    SqlChatThread,
    current_workspace_id,
)
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch_us,
    run_write_transaction,
)
from omnigent.server.chat_mentions import parse_mentions
from omnigent.stores.chat_store import (
    ChatChannel,
    ChatMember,
    ChatMessage,
    ChatStore,
    ChatThread,
    DmResult,
    DmStatus,
    MessagePage,
    PostMessageResult,
    PostStatus,
)

_MAX_PARTICIPANTS = 5


def _to_channel(row: SqlChatChannel) -> ChatChannel:
    """Copy a channel row into a detached value."""
    return ChatChannel(
        id=row.id,
        kind=row.kind,
        name=row.name,
        topic=row.topic,
        project_name=row.project_name,
        visibility=row.visibility,
        dm_key=row.dm_key,
        session_root_id=row.session_root_id,
        created_by=row.created_by,
        created_at=row.created_at,
        archived_at=row.archived_at,
    )


def _to_member(row: SqlChatMember) -> ChatMember:
    """Copy a member row into a detached value."""
    return ChatMember(
        channel_id=row.channel_id,
        principal_id=row.principal_id,
        role=row.role,
        joined_at=row.joined_at,
    )


def _to_message(row: SqlChatMessage) -> ChatMessage:
    """Copy a message row into a detached value."""
    return ChatMessage(
        id=row.id,
        channel_id=row.channel_id,
        thread_root_id=row.thread_root_id,
        author_id=row.author_id,
        author_kind=row.author_kind,
        created_at=row.created_at,
        body=row.body,
        client_id=row.client_id,
    )


def _to_thread(row: SqlChatThread) -> ChatThread:
    """Copy a thread row into a detached value."""
    return ChatThread(
        root_message_id=row.root_message_id,
        channel_id=row.channel_id,
        reply_count=row.reply_count,
        last_reply_at=row.last_reply_at,
        participants=tuple(json.loads(row.participants or "[]")),
    )


def _after_predicate(cursor: tuple[int, str]):
    """``(created_at, id) > cursor`` comparison."""
    created_at, message_id = cursor
    return or_(
        SqlChatMessage.created_at > created_at,
        and_(
            SqlChatMessage.created_at == created_at,
            SqlChatMessage.id > message_id,
        ),
    )


def _before_predicate(cursor: tuple[int, str]):
    """``(created_at, id) < cursor`` comparison."""
    created_at, message_id = cursor
    return or_(
        SqlChatMessage.created_at < created_at,
        and_(
            SqlChatMessage.created_at == created_at,
            SqlChatMessage.id < message_id,
        ),
    )


class SqlAlchemyChatStore(ChatStore):
    """SQLAlchemy implementation of :class:`ChatStore`."""

    def __init__(self, storage_location: str) -> None:
        """Open the store against one database URI."""
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.chat_store",
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.chat_store",
            immediate=True,
        )

    # ── Channels ────────────────────────────────────────────────

    def bootstrap_channel(self, name: str, created_by: str | None = None) -> ChatChannel:
        """Ensure a named channel exists. See the base class."""

        def _insert(session: Session) -> ChatChannel:
            channel_id = uuid.uuid4().hex
            created_at = now_epoch_us()
            row = SqlChatChannel(
                id=channel_id,
                kind="channel",
                name=name,
                topic=None,
                project_name=None,
                visibility="public",
                dm_key=None,
                session_root_id=None,
                created_by=created_by,
                created_at=created_at,
                archived_at=None,
            )
            session.add(row)
            if created_by is not None:
                session.add(
                    SqlChatMember(
                        channel_id=channel_id,
                        principal_id=created_by,
                        role="member",
                        joined_at=created_at,
                    )
                )
            return _to_channel(row)

        try:
            return run_write_transaction(
                self._session_immediate, "bootstrap_chat_channel", _insert
            )
        except IntegrityError:
            # Lost the race on the unique (workspace_id, name) index.
            return self._attach_bootstrap_member(name, created_by)

    def _attach_bootstrap_member(self, name: str, created_by: str | None) -> ChatChannel:
        """Attach the creator to the channel that won the bootstrap race."""

        def _attach(session: Session) -> ChatChannel:
            row = session.execute(
                select(SqlChatChannel).where(
                    SqlChatChannel.workspace_id == current_workspace_id(),
                    SqlChatChannel.kind == "channel",
                    SqlChatChannel.name == name,
                )
            ).scalar_one_or_none()
            if row is None:
                raise RuntimeError("bootstrap channel vanished after conflict")
            _ensure_member(session, row.id, created_by)
            return _to_channel(row)

        try:
            return run_write_transaction(
                self._session_immediate, "bootstrap_chat_channel_attach", _attach
            )
        except IntegrityError:
            # Another attach added the same member concurrently.
            with self._session("bootstrap_chat_channel_read") as session:
                row = session.execute(
                    select(SqlChatChannel).where(
                        SqlChatChannel.workspace_id == current_workspace_id(),
                        SqlChatChannel.kind == "channel",
                        SqlChatChannel.name == name,
                    )
                ).scalar_one_or_none()
                if row is None:
                    raise RuntimeError("bootstrap channel vanished after conflict") from None
                return _to_channel(row)

    def get_channel(self, channel_id: str) -> ChatChannel | None:
        """Return a channel by id. See the base class."""
        with self._session("get_chat_channel") as session:
            row = session.get(SqlChatChannel, (current_workspace_id(), channel_id))
            return _to_channel(row) if row is not None else None

    def add_member(self, channel_id: str, principal_id: str, role: str = "member") -> ChatMember:
        """Add a member, keeping an existing row. See the base class."""

        def _write(session: Session) -> ChatMember:
            _ensure_member(session, channel_id, principal_id, role)
            row = session.execute(
                select(SqlChatMember).where(
                    SqlChatMember.workspace_id == current_workspace_id(),
                    SqlChatMember.channel_id == channel_id,
                    SqlChatMember.principal_id == principal_id,
                )
            ).scalar_one()
            return _to_member(row)

        return run_write_transaction(self._session_immediate, "add_chat_member", _write)

    # ── DMs ─────────────────────────────────────────────────────

    def get_or_create_dm(self, principals: list[str]) -> DmResult:
        """Open or create a DM. See the base class."""
        participants = sorted(set(principals))
        dm_key = hashlib.sha256(",".join(participants).encode("utf-8")).hexdigest()

        def _write(session: Session) -> DmResult:
            existing = session.execute(
                select(SqlChatChannel).where(
                    SqlChatChannel.workspace_id == current_workspace_id(),
                    SqlChatChannel.dm_key == dm_key,
                )
            ).scalar_one_or_none()
            if existing is not None:
                return DmResult(DmStatus.EXISTING, _to_channel(existing))

            if _agent_owner_outside(session, participants) is not None:
                return DmResult(DmStatus.AGENT_OWNER_REQUIRED)

            channel_id = uuid.uuid4().hex
            created_at = now_epoch_us()
            row = SqlChatChannel(
                id=channel_id,
                kind="dm",
                name=None,
                topic=None,
                project_name=None,
                visibility="public",
                dm_key=dm_key,
                session_root_id=None,
                created_by=None,
                created_at=created_at,
                archived_at=None,
            )
            session.add(row)
            for principal in participants:
                session.add(
                    SqlChatMember(
                        channel_id=channel_id,
                        principal_id=principal,
                        role="member",
                        joined_at=created_at,
                    )
                )
            return DmResult(DmStatus.CREATED, _to_channel(row))

        try:
            return run_write_transaction(self._session_immediate, "open_chat_dm", _write)
        except IntegrityError:
            # A concurrent open won the unique dm_key race.
            with self._session("read_chat_dm") as session:
                existing = session.execute(
                    select(SqlChatChannel).where(
                        SqlChatChannel.workspace_id == current_workspace_id(),
                        SqlChatChannel.dm_key == dm_key,
                    )
                ).scalar_one_or_none()
                if existing is None:
                    raise
                return DmResult(DmStatus.EXISTING, _to_channel(existing))

    # ── Messages ────────────────────────────────────────────────

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
        """Store one message. See the base class."""
        handles = omnigent_agent_handles if omnigent_agent_handles is not None else ()

        def _write(session: Session) -> PostMessageResult:
            if client_id is not None:
                existing = session.execute(
                    select(SqlChatMessage).where(
                        SqlChatMessage.workspace_id == current_workspace_id(),
                        SqlChatMessage.channel_id == channel_id,
                        SqlChatMessage.author_id == author_id,
                        SqlChatMessage.client_id == client_id,
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    if existing.body == body:
                        return PostMessageResult(PostStatus.REPLAYED, _to_message(existing))
                    return PostMessageResult(PostStatus.BODY_CONFLICT)

            if thread_root_id is not None:
                root = session.execute(
                    select(SqlChatMessage).where(
                        SqlChatMessage.workspace_id == current_workspace_id(),
                        SqlChatMessage.channel_id == channel_id,
                        SqlChatMessage.id == thread_root_id,
                    )
                ).scalar_one_or_none()
                if root is None:
                    return PostMessageResult(PostStatus.THREAD_ROOT_NOT_FOUND)
                if root.thread_root_id is not None:
                    return PostMessageResult(PostStatus.NESTED_THREAD)
            else:
                root = None

            message_id = uuid.uuid4().hex
            created_at = now_epoch_us()
            row = SqlChatMessage(
                id=message_id,
                channel_id=channel_id,
                thread_root_id=thread_root_id,
                author_id=author_id,
                author_kind=author_kind,
                created_at=created_at,
                body=body,
                client_id=client_id,
            )
            session.add(row)
            # Flush the message now so a message-insert IntegrityError
            # (e.g. a lost client_id race) surfaces here, uncaught, rather
            # than inside _update_thread's thread-INSERT recovery.
            session.flush()

            if root is not None:
                _update_thread(session, channel_id, root.id, author_id, created_at)

            _write_mentions(session, channel_id, message_id, created_at, body, handles)

            return PostMessageResult(PostStatus.CREATED, _to_message(row))

        return run_write_transaction(self._session_immediate, "post_chat_message", _write)

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
        """List messages ascending. See the base class."""
        with self._session("list_chat_messages") as session:
            filtered = select(SqlChatMessage).where(
                SqlChatMessage.workspace_id == current_workspace_id(),
                SqlChatMessage.channel_id == channel_id,
            )
            if thread_root_id is None:
                filtered = filtered.where(SqlChatMessage.thread_root_id.is_(None))
            else:
                filtered = filtered.where(SqlChatMessage.thread_root_id == thread_root_id)

            query = filtered
            if after is not None:
                query = query.where(_after_predicate(after))
            if before is not None:
                query = query.where(_before_predicate(before))

            if tail is not None:
                rows = list(
                    reversed(
                        session.execute(
                            query.order_by(
                                SqlChatMessage.created_at.desc(),
                                SqlChatMessage.id.desc(),
                            ).limit(tail)
                        )
                        .scalars()
                        .all()
                    )
                )
            elif before is not None:
                rows = list(
                    reversed(
                        session.execute(
                            query.order_by(
                                SqlChatMessage.created_at.desc(),
                                SqlChatMessage.id.desc(),
                            ).limit(limit)
                        )
                        .scalars()
                        .all()
                    )
                )
            else:
                rows = list(
                    session.execute(
                        query.order_by(
                            SqlChatMessage.created_at.asc(),
                            SqlChatMessage.id.asc(),
                        ).limit(limit)
                    )
                    .scalars()
                    .all()
                )

            entities = [_to_message(row) for row in rows]
            has_older = False
            has_more = False
            next_after: tuple[int, str] | None = None
            next_before: tuple[int, str] | None = None
            if entities:
                first, last = entities[0], entities[-1]
                has_older = _has_before(session, filtered, first)
                has_more = _has_after(session, filtered, last)
                next_after = (last.created_at, last.id)
                next_before = (first.created_at, first.id)

            return MessagePage(
                items=tuple(entities),
                has_older=has_older,
                has_more=has_more,
                next_after=next_after,
                next_before=next_before,
            )

    # ── Threads ─────────────────────────────────────────────────

    def get_thread(self, root_message_id: str) -> ChatThread | None:
        """Return thread state for a root message. See the base class."""
        with self._session("get_chat_thread") as session:
            row = session.get(SqlChatThread, (current_workspace_id(), root_message_id))
            return _to_thread(row) if row is not None else None


def _ensure_member(
    session: Session, channel_id: str, principal_id: str | None, role: str = "member"
) -> None:
    """Insert a member row unless it already exists."""
    if principal_id is None:
        return
    present = session.execute(
        select(SqlChatMember.channel_id).where(
            SqlChatMember.workspace_id == current_workspace_id(),
            SqlChatMember.channel_id == channel_id,
            SqlChatMember.principal_id == principal_id,
        )
    ).first()
    if present is None:
        session.add(
            SqlChatMember(
                channel_id=channel_id,
                principal_id=principal_id,
                role=role,
                joined_at=now_epoch_us(),
            )
        )


def _agent_owner_outside(session: Session, principals: list[str]) -> str | None:
    """Return an agent principal whose owner is not in *principals*."""
    rows = session.execute(
        select(
            SqlChatAgentAccount.principal_id,
            SqlChatAgentAccount.owner_id,
        ).where(
            SqlChatAgentAccount.workspace_id == current_workspace_id(),
            SqlChatAgentAccount.principal_id.in_(principals),
            SqlChatAgentAccount.revoked_at.is_(None),
        )
    ).all()
    for principal_id, owner_id in rows:
        if owner_id not in principals:
            return principal_id
    return None


def _update_thread(
    session: Session,
    channel_id: str,
    root_message_id: str,
    author_id: str,
    created_at: int,
) -> None:
    """Bump a root message's reply counter and latest participants.

    The existing row is read under ``SELECT ... FOR UPDATE`` so concurrent
    replies on Postgres/MySQL serialize on the row instead of both reading
    ``reply_count`` and writing ``N+1``. The first reply for a root has no row
    to lock, so it INSERTs; when two first replies race, the loser's INSERT
    raises ``IntegrityError``. That is recovered by rolling back only the
    INSERT (the message was already flushed) and re-reading the winning row
    under the same lock.
    """

    def _apply(thread: SqlChatThread) -> None:
        participants = json.loads(thread.participants or "[]")
        participants = [author_id] + [p for p in participants if p != author_id]
        thread.participants = json.dumps(participants[:_MAX_PARTICIPANTS])
        thread.reply_count = thread.reply_count + 1
        thread.last_reply_at = created_at
        thread.updated_at = created_at

    thread = session.execute(
        select(SqlChatThread)
        .where(
            SqlChatThread.workspace_id == current_workspace_id(),
            SqlChatThread.root_message_id == root_message_id,
        )
        .with_for_update()
    ).scalar_one_or_none()

    if thread is not None:
        _apply(thread)
        return

    thread = SqlChatThread(
        root_message_id=root_message_id,
        channel_id=channel_id,
        reply_count=0,
        last_reply_at=None,
        participants="[]",
        agent_state="none",
        updated_at=created_at,
    )
    _apply(thread)
    try:
        with session.begin_nested():
            session.add(thread)
            session.flush()
    except IntegrityError:
        # Lost the first-reply INSERT race. The nested transaction rolled
        # back only the failed INSERT, so re-read the winner with FOR UPDATE
        # and apply the counter/participant update to it.
        thread = session.execute(
            select(SqlChatThread)
            .where(
                SqlChatThread.workspace_id == current_workspace_id(),
                SqlChatThread.root_message_id == root_message_id,
            )
            .with_for_update()
        ).scalar_one()
        _apply(thread)


def _write_mentions(
    session: Session,
    channel_id: str,
    message_id: str,
    created_at: int,
    body: str,
    handles: Collection[str],
) -> None:
    """Validate mention tokens and write one row per valid target."""
    tokens = parse_mentions(body)
    if not tokens:
        return

    # An id that starts with "agent:" is an agent account, never a user
    # mention target, even when that id is also a channel member.
    user_ids = [
        t.target_id for t in tokens if t.kind == "user" and not t.target_id.startswith("agent:")
    ]
    account_ids = [t.target_id for t in tokens if t.kind == "agent_account"]

    valid_users: set[str] = set()
    if user_ids:
        # The human directory is the members of the "general" channel plus
        # active agent accounts. A user mention is therefore valid when the
        # target is a member of the posting channel or of "general".
        member_channel_ids = [channel_id]
        general_id = session.execute(
            select(SqlChatChannel.id).where(
                SqlChatChannel.workspace_id == current_workspace_id(),
                SqlChatChannel.kind == "channel",
                SqlChatChannel.name == "general",
            )
        ).scalar_one_or_none()
        if general_id is not None and general_id != channel_id:
            member_channel_ids.append(general_id)
        valid_users = set(
            session.execute(
                select(SqlChatMember.principal_id).where(
                    SqlChatMember.workspace_id == current_workspace_id(),
                    SqlChatMember.channel_id.in_(member_channel_ids),
                    SqlChatMember.principal_id.in_(user_ids),
                )
            ).scalars()
        )

    valid_accounts: set[str] = set()
    if account_ids:
        valid_accounts = set(
            session.execute(
                select(SqlChatAgentAccount.principal_id).where(
                    SqlChatAgentAccount.workspace_id == current_workspace_id(),
                    SqlChatAgentAccount.principal_id.in_(account_ids),
                    SqlChatAgentAccount.revoked_at.is_(None),
                )
            ).scalars()
        )

    seen: set[str] = set()
    for token in tokens:
        if token.kind == "user":
            valid = token.target_id in valid_users
        elif token.kind == "agent_account":
            valid = token.target_id in valid_accounts
        else:
            valid = token.target_id in handles
        if valid and token.target_id not in seen:
            seen.add(token.target_id)
            session.add(
                SqlChatMention(
                    message_id=message_id,
                    channel_id=channel_id,
                    target_kind=token.kind,
                    target_id=token.target_id,
                    created_at=created_at,
                )
            )


def _has_before(
    session: Session, filtered: Select[tuple[SqlChatMessage]], first: ChatMessage
) -> bool:
    """Whether any message precedes *first* within *filtered*."""
    stmt = filtered.where(_before_predicate((first.created_at, first.id))).limit(1)
    return session.execute(stmt).first() is not None


def _has_after(
    session: Session, filtered: Select[tuple[SqlChatMessage]], last: ChatMessage
) -> bool:
    """Whether any message follows *last* within *filtered*."""
    stmt = filtered.where(_after_predicate((last.created_at, last.id))).limit(1)
    return session.execute(stmt).first() is not None
