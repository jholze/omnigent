"""SQLAlchemy team-chat store."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Collection

from sqlalchemy import Select, and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from omnigent.db.db_models import (
    SqlChatAgentAccount,
    SqlChatChannel,
    SqlChatMember,
    SqlChatMention,
    SqlChatMessage,
    SqlChatRead,
    SqlChatThread,
    SqlUser,
    current_workspace_id,
)
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch_us,
    run_write_transaction,
)
from omnigent.server.agent_account_tokens import mint_agent_token
from omnigent.server.chat_access import principal_kind
from omnigent.server.chat_mentions import parse_mentions
from omnigent.stores.chat_store import (
    AccountStatus,
    AccountWriteResult,
    AgentTokenIssue,
    ChangesPage,
    ChatAgentAccount,
    ChatChannel,
    ChatMember,
    ChatMessage,
    ChatStore,
    ChatThread,
    DmResult,
    DmStatus,
    MarkReadResult,
    MessagePage,
    PostMessageResult,
    PostStatus,
    ReadCursor,
    ReadStatus,
    UnreadMessage,
)

_MAX_PARTICIPANTS = 5
_BOT_STREAK_LIMIT = 5
_AGENT_ACCOUNT_LIMIT = 5
_LAST_SEEN_INTERVAL_US = 60_000_000
_BRAKE_BODY = "Bots warten auf einen Menschen"
_BRAKE_CODE = "bot_streak_limit"
_BOT_AUTHOR_KINDS = frozenset({"agent_account", "agent", "omnigent_agent"})
_HUMAN_AUTHOR_KINDS = frozenset({"user", "human"})
_UNREAD_LIMIT = 50
_THREAD_SCAN = 30
_REASON_RANK = {"mention": 0, "dm": 1, "thread_reply": 2}


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


def _to_agent_account(row: SqlChatAgentAccount) -> ChatAgentAccount:
    """Copy an agent-account row into a detached value. The raw token is omitted."""
    return ChatAgentAccount(
        id=row.id,
        principal_id=row.principal_id,
        owner_id=row.owner_id,
        display_name=row.display_name,
        handle=row.handle,
        token_prefix=row.token_prefix,
        secret_version=row.secret_version,
        last_seen_at=row.last_seen_at,
        revoked_at=row.revoked_at,
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

    def get_channel_by_name(self, name: str) -> ChatChannel | None:
        """Return a public channel by name. See the base class."""
        with self._session("get_chat_channel_by_name") as session:
            row = session.execute(
                select(SqlChatChannel).where(
                    SqlChatChannel.workspace_id == current_workspace_id(),
                    SqlChatChannel.kind == "channel",
                    SqlChatChannel.name == name,
                )
            ).scalar_one_or_none()
            return _to_channel(row) if row is not None else None

    def is_member(self, channel_id: str, principal_id: str) -> bool:
        """Whether a principal is a member. See the base class."""
        with self._session("get_chat_membership") as session:
            row = session.execute(
                select(SqlChatMember.channel_id).where(
                    SqlChatMember.workspace_id == current_workspace_id(),
                    SqlChatMember.channel_id == channel_id,
                    SqlChatMember.principal_id == principal_id,
                )
            ).first()
            return row is not None

    def list_joined_channels(self, principal_id: str) -> tuple[ChatChannel, ...]:
        """Public channels a principal joined. See the base class."""
        with self._session("list_joined_chat_channels") as session:
            member_channel_ids = select(SqlChatMember.channel_id).where(
                SqlChatMember.workspace_id == current_workspace_id(),
                SqlChatMember.principal_id == principal_id,
            )
            rows = (
                session.execute(
                    select(SqlChatChannel)
                    .where(
                        SqlChatChannel.workspace_id == current_workspace_id(),
                        SqlChatChannel.kind == "channel",
                        SqlChatChannel.id.in_(member_channel_ids),
                    )
                    .order_by(SqlChatChannel.created_at.asc(), SqlChatChannel.id.asc())
                )
                .scalars()
                .all()
            )
            return tuple(_to_channel(row) for row in rows)

    def list_joined_dms(self, principal_id: str) -> tuple[ChatChannel, ...]:
        """Direct messages a principal participates in. See the base class."""
        with self._session("list_joined_chat_dms") as session:
            member_channel_ids = select(SqlChatMember.channel_id).where(
                SqlChatMember.workspace_id == current_workspace_id(),
                SqlChatMember.principal_id == principal_id,
            )
            rows = (
                session.execute(
                    select(SqlChatChannel)
                    .where(
                        SqlChatChannel.workspace_id == current_workspace_id(),
                        SqlChatChannel.kind == "dm",
                        SqlChatChannel.id.in_(member_channel_ids),
                    )
                    .order_by(SqlChatChannel.created_at.asc(), SqlChatChannel.id.asc())
                )
                .scalars()
                .all()
            )
            return tuple(_to_channel(row) for row in rows)

    def list_members(self, channel_id: str) -> tuple[ChatMember, ...]:
        """Members of a channel. See the base class."""
        with self._session("list_chat_members") as session:
            rows = (
                session.execute(
                    select(SqlChatMember)
                    .where(
                        SqlChatMember.workspace_id == current_workspace_id(),
                        SqlChatMember.channel_id == channel_id,
                    )
                    .order_by(SqlChatMember.joined_at.asc(), SqlChatMember.principal_id.asc())
                )
                .scalars()
                .all()
            )
            return tuple(_to_member(row) for row in rows)

    def remove_member(self, channel_id: str, principal_id: str) -> bool:
        """Drop a membership. See the base class."""

        def _write(session: Session) -> bool:
            row = session.execute(
                select(SqlChatMember).where(
                    SqlChatMember.workspace_id == current_workspace_id(),
                    SqlChatMember.channel_id == channel_id,
                    SqlChatMember.principal_id == principal_id,
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            session.delete(row)
            return True

        return run_write_transaction(self._session_immediate, "remove_chat_member", _write)

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

    # ── Directory ───────────────────────────────────────────────

    def list_active_agent_accounts(self) -> tuple[ChatAgentAccount, ...]:
        """Active agent accounts. See the base class."""
        with self._session("list_chat_agent_accounts") as session:
            rows = (
                session.execute(
                    select(SqlChatAgentAccount)
                    .where(
                        SqlChatAgentAccount.workspace_id == current_workspace_id(),
                        SqlChatAgentAccount.revoked_at.is_(None),
                    )
                    .order_by(SqlChatAgentAccount.created_at.asc(), SqlChatAgentAccount.id.asc())
                )
                .scalars()
                .all()
            )
            return tuple(_to_agent_account(row) for row in rows)

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

            streak = _bot_streak(session, channel_id, thread_root_id)
            if _is_bot_author(author_kind) and streak >= _BOT_STREAK_LIMIT:
                notice = _post_brake_notice_once(session, channel_id, thread_root_id)
                return PostMessageResult(PostStatus.BOT_STREAK_LIMIT, notice=notice)

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
                _update_thread(session, channel_id, root.id, author_id, author_kind, created_at)

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

    # ── Changes ─────────────────────────────────────────────────

    def list_changes(
        self,
        principal_id: str,
        *,
        after: tuple[int, str] | None = None,
        limit: int = 1000,
    ) -> ChangesPage:
        """Return a caller's change feed. See the base class."""
        with self._session("list_chat_changes") as session:
            member_channel_ids = select(SqlChatMember.channel_id).where(
                SqlChatMember.workspace_id == current_workspace_id(),
                SqlChatMember.principal_id == principal_id,
            )
            filtered = select(SqlChatMessage).where(
                SqlChatMessage.workspace_id == current_workspace_id(),
                SqlChatMessage.channel_id.in_(member_channel_ids),
            )
            if after is not None:
                filtered = filtered.where(_after_predicate(after))
            rows = list(
                session.execute(
                    filtered.order_by(
                        SqlChatMessage.created_at.asc(),
                        SqlChatMessage.id.asc(),
                    ).limit(limit + 1)
                )
                .scalars()
                .all()
            )
            reset = len(rows) > limit
            items = rows[:limit]
            next_after = (items[-1].created_at, items[-1].id) if items else None
            return ChangesPage(
                items=tuple(_to_message(row) for row in items),
                next_after=next_after,
                reset=reset,
            )

    # ── Threads ─────────────────────────────────────────────────

    def get_thread(self, root_message_id: str) -> ChatThread | None:
        """Return thread state for a root message. See the base class."""
        with self._session("get_chat_thread") as session:
            row = session.get(SqlChatThread, (current_workspace_id(), root_message_id))
            return _to_thread(row) if row is not None else None

    # ── Agent accounts ──────────────────────────────────────────

    def create_agent_account(
        self,
        owner_id: str,
        display_name: str,
        handle: str,
        *,
        webhook_url: str = "",
    ) -> AccountWriteResult:
        """Mint an agent account. See the base class."""
        raw, token_hash, token_prefix = mint_agent_token()
        account_id = uuid.uuid4().hex
        principal_id = f"agent:{account_id}"
        created_at = now_epoch_us()

        def _write(session: Session) -> AccountWriteResult:
            active = session.scalar(
                select(func.count())
                .select_from(SqlChatAgentAccount)
                .where(
                    SqlChatAgentAccount.workspace_id == current_workspace_id(),
                    SqlChatAgentAccount.owner_id == owner_id,
                    SqlChatAgentAccount.revoked_at.is_(None),
                )
            )
            if active is not None and int(active) >= _AGENT_ACCOUNT_LIMIT:
                return AccountWriteResult(AccountStatus.LIMIT)
            row = SqlChatAgentAccount(
                id=account_id,
                principal_id=principal_id,
                owner_id=owner_id,
                display_name=display_name,
                handle=handle,
                token_hash=token_hash,
                token_prefix=token_prefix,
                secret_version=1,
                webhook_url=webhook_url,
                created_at=created_at,
            )
            session.add(row)
            session.flush()
            return AccountWriteResult(
                AccountStatus.CREATED,
                AgentTokenIssue(_to_agent_account(row), raw),
            )

        try:
            return run_write_transaction(
                self._session_immediate, "create_chat_agent_account", _write
            )
        except IntegrityError:
            return AccountWriteResult(AccountStatus.HANDLE_TAKEN)

    def rotate_agent_token(self, account_id: str, owner_id: str) -> AccountWriteResult:
        """Replace the token hash. See the base class."""
        raw, token_hash, token_prefix = mint_agent_token()

        def _write(session: Session) -> AccountWriteResult:
            row = _owned_account(session, account_id, owner_id)
            if row is None:
                return AccountWriteResult(AccountStatus.NOT_FOUND)
            if row.revoked_at is not None:
                return AccountWriteResult(AccountStatus.REVOKED_ACCOUNT)
            row.token_hash = token_hash
            row.token_prefix = token_prefix
            row.secret_version = row.secret_version + 1
            session.flush()
            return AccountWriteResult(
                AccountStatus.ROTATED,
                AgentTokenIssue(_to_agent_account(row), raw),
            )

        try:
            return run_write_transaction(
                self._session_immediate, "rotate_chat_agent_token", _write
            )
        except IntegrityError:
            return AccountWriteResult(AccountStatus.NOT_FOUND)

    def revoke_agent_account(self, account_id: str, owner_id: str) -> AccountWriteResult:
        """Revoke an agent account. See the base class."""

        def _write(session: Session) -> AccountWriteResult:
            row = _owned_account(session, account_id, owner_id)
            if row is None:
                return AccountWriteResult(AccountStatus.NOT_FOUND)
            if row.revoked_at is None:
                row.revoked_at = now_epoch_us()
            session.execute(
                delete(SqlChatMember).where(
                    SqlChatMember.workspace_id == current_workspace_id(),
                    SqlChatMember.principal_id == row.principal_id,
                )
            )
            return AccountWriteResult(AccountStatus.REVOKED, None)

        return run_write_transaction(self._session_immediate, "revoke_chat_agent_account", _write)

    def principal_for_token_hash(self, token_hash: str) -> str | None:
        """Resolve a live token hash. See the base class."""
        account = self.account_for_token_hash(token_hash)
        return account.principal_id if account is not None else None

    def account_for_token_hash(self, token_hash: str) -> ChatAgentAccount | None:
        """Resolve a live token hash to the account. See the base class."""
        with self._session("lookup_chat_agent_token") as session:
            row = session.execute(
                select(SqlChatAgentAccount).where(
                    SqlChatAgentAccount.token_hash == token_hash,
                    SqlChatAgentAccount.revoked_at.is_(None),
                )
            ).scalar_one_or_none()
            return _to_agent_account(row) if row is not None else None

    def owner_account_live(self, owner_id: str) -> bool:
        """Whether the owner still has a live accounts row. See the base class."""
        with self._session("check_chat_agent_owner") as session:
            row = session.execute(
                select(SqlUser.deleted_at).where(
                    SqlUser.workspace_id == current_workspace_id(),
                    SqlUser.id == owner_id,
                )
            ).first()
        if row is None:
            return True
        return row[0] is None

    def count_sessions_started_by(self, account_id: str, since_us: int) -> int:
        """Count bot-started sessions since *since_us*. See the base class."""
        with self._session("count_bot_started_sessions") as session:
            count = session.scalar(
                select(func.count())
                .select_from(SqlChatThread)
                .where(
                    SqlChatThread.workspace_id == current_workspace_id(),
                    SqlChatThread.started_by_agent_account_id == account_id,
                    SqlChatThread.session_id.is_not(None),
                    SqlChatThread.start_requested_at >= since_us,
                )
            )
        return int(count or 0)

    def get_agent_account_by_principal(self, principal_id: str) -> ChatAgentAccount | None:
        """Return a live account. See the base class."""
        with self._session("get_chat_agent_account") as session:
            row = session.execute(
                select(SqlChatAgentAccount).where(
                    SqlChatAgentAccount.workspace_id == current_workspace_id(),
                    SqlChatAgentAccount.principal_id == principal_id,
                    SqlChatAgentAccount.revoked_at.is_(None),
                )
            ).scalar_one_or_none()
            return _to_agent_account(row) if row is not None else None

    def touch_agent_last_seen(self, principal_id: str) -> None:
        """Stamp presence at most once per 60 seconds. See the base class."""
        seen_at = now_epoch_us()
        cutoff = seen_at - _LAST_SEEN_INTERVAL_US

        def _write(session: Session) -> None:
            session.execute(
                update(SqlChatAgentAccount)
                .where(
                    SqlChatAgentAccount.workspace_id == current_workspace_id(),
                    SqlChatAgentAccount.principal_id == principal_id,
                    SqlChatAgentAccount.revoked_at.is_(None),
                    or_(
                        SqlChatAgentAccount.last_seen_at.is_(None),
                        SqlChatAgentAccount.last_seen_at < cutoff,
                    ),
                )
                .values(last_seen_at=seen_at)
            )

        run_write_transaction(self._session_immediate, "touch_chat_agent_last_seen", _write)

    # ── Read cursors ────────────────────────────────────────────

    def mark_read(
        self,
        channel_id: str,
        principal_id: str,
        message_id: str,
        *,
        thread_root_id: str | None = None,
        created_at: int | None = None,
    ) -> MarkReadResult:
        """Advance a cursor forward. See the base class."""
        scope = thread_root_id or ""

        def _write(session: Session) -> MarkReadResult:
            message = session.get(SqlChatMessage, (current_workspace_id(), message_id))
            if message is None or message.channel_id != channel_id:
                return MarkReadResult(ReadStatus.NOT_FOUND)
            if created_at is not None and message.created_at != created_at:
                return MarkReadResult(ReadStatus.NOT_FOUND)
            if scope == "":
                if message.thread_root_id is not None:
                    return MarkReadResult(ReadStatus.WRONG_SCOPE)
            elif message.id != scope and message.thread_root_id != scope:
                return MarkReadResult(ReadStatus.WRONG_SCOPE)
            row = session.get(
                SqlChatRead,
                (current_workspace_id(), channel_id, principal_id, scope),
            )
            if row is not None and _cursor_covers(row, message):
                return MarkReadResult(ReadStatus.UNCHANGED, _to_cursor(row))
            now = now_epoch_us()
            if row is None:
                row = SqlChatRead(
                    channel_id=channel_id,
                    principal_id=principal_id,
                    thread_root_id=scope,
                    last_read_at=message.created_at,
                    last_read_id=message.id,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.last_read_at = message.created_at
                row.last_read_id = message.id
                row.updated_at = now
            return MarkReadResult(ReadStatus.ADVANCED, _to_cursor(row))

        return run_write_transaction(self._session_immediate, "mark_chat_read", _write)

    def list_unread(
        self,
        principal_id: str,
        *,
        limit: int = 50,
        channel_ids: Collection[str] | None = None,
    ) -> tuple[UnreadMessage, ...]:
        """Return the caller's unread wake items. See the base class."""
        capped = min(max(limit, 1), _UNREAD_LIMIT)
        with self._session("list_chat_unread") as session:
            member_ids = set(
                session.execute(
                    select(SqlChatMember.channel_id).where(
                        SqlChatMember.workspace_id == current_workspace_id(),
                        SqlChatMember.principal_id == principal_id,
                    )
                ).scalars()
            )
            if channel_ids is not None:
                member_ids &= set(channel_ids)
            if principal_kind(principal_id) == "agent_account":
                member_ids &= _owner_channel_ids(session, principal_id)
            if not member_ids:
                return ()
            cursors = _load_cursors(session, principal_id)
            found: dict[str, tuple[ChatMessage, str]] = {}

            def consider(row: SqlChatMessage, reason: str) -> None:
                if row.author_id == principal_id or row.channel_id not in member_ids:
                    return
                if not _message_is_unread(row, cursors):
                    return
                current = found.get(row.id)
                if current is None or _REASON_RANK[reason] < _REASON_RANK[current[1]]:
                    found[row.id] = (_to_message(row), reason)

            for channel_id in member_ids:
                _collect_channel_unread(session, principal_id, channel_id, cursors, consider)
            ordered = sorted(found.values(), key=lambda item: (item[0].created_at, item[0].id))
            return tuple(
                UnreadMessage(message=message, reason=reason)
                for message, reason in ordered[:capped]
            )

    def channel_unread_counts(
        self, principal_id: str, channel_ids: Collection[str]
    ) -> dict[str, tuple[int, int]]:
        """Count unread top-level messages and mentions. See the base class."""
        ids = list(channel_ids)
        if not ids:
            return {}
        with self._session("count_chat_unread") as session:
            cursors = _load_cursors(session, principal_id)
            counts: dict[str, tuple[int, int]] = {}
            for channel_id in ids:
                cursor = cursors.get((channel_id, ""))
                base = (
                    SqlChatMessage.workspace_id == current_workspace_id(),
                    SqlChatMessage.channel_id == channel_id,
                    SqlChatMessage.thread_root_id.is_(None),
                    SqlChatMessage.author_id != principal_id,
                )
                unread_query = select(func.count()).select_from(SqlChatMessage).where(*base)
                after = _after_cursor(cursor)
                if after is not None:
                    unread_query = unread_query.where(after)
                unread = int(session.scalar(unread_query) or 0)
                mention_query = (
                    select(func.count())
                    .select_from(SqlChatMessage)
                    .join(
                        SqlChatMention,
                        and_(
                            SqlChatMention.workspace_id == SqlChatMessage.workspace_id,
                            SqlChatMention.message_id == SqlChatMessage.id,
                        ),
                    )
                    .where(
                        *base,
                        SqlChatMention.target_id == principal_id,
                    )
                )
                if after is not None:
                    mention_query = mention_query.where(after)
                mentions = int(session.scalar(mention_query) or 0)
                counts[channel_id] = (unread, mentions)
            return counts


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
    author_kind: str,
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
        if author_kind in _BOT_AUTHOR_KINDS:
            thread.bot_streak = (thread.bot_streak or 0) + 1
        elif author_kind in _HUMAN_AUTHOR_KINDS:
            thread.bot_streak = 0
            thread.brake_notice_at = None

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
        bot_streak=0,
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


def _is_bot_author(author_kind: str) -> bool:
    """Whether *author_kind* continues a bot streak."""
    return author_kind in _BOT_AUTHOR_KINDS


def _insert_system_message(
    session: Session,
    channel_id: str,
    thread_root_id: str | None,
    body: str,
    system_code: str,
) -> ChatMessage:
    """Insert one system row. It does not continue a bot streak."""
    created_at = now_epoch_us()
    row = SqlChatMessage(
        id=uuid.uuid4().hex,
        channel_id=channel_id,
        thread_root_id=thread_root_id,
        author_id="system",
        author_kind="system",
        created_at=created_at,
        body=body,
        system_code=system_code,
    )
    session.add(row)
    session.flush()
    return _to_message(row)


def _channel_brake_posted(session: Session, channel_id: str) -> bool:
    """Whether the current top-level bot run already has the brake line."""
    rows = session.execute(
        select(SqlChatMessage.author_kind, SqlChatMessage.system_code)
        .where(
            SqlChatMessage.workspace_id == current_workspace_id(),
            SqlChatMessage.channel_id == channel_id,
            SqlChatMessage.thread_root_id.is_(None),
        )
        .order_by(SqlChatMessage.created_at.desc(), SqlChatMessage.id.desc())
        .limit(20)
    ).all()
    for kind, code in rows:
        if kind in _HUMAN_AUTHOR_KINDS:
            return False
        if code == _BRAKE_CODE:
            return True
    return False


def _post_brake_notice_once(
    session: Session, channel_id: str, thread_root_id: str | None
) -> ChatMessage | None:
    """Post ``Bots warten auf einen Menschen`` once for the current streak."""
    if thread_root_id is not None:
        thread = session.execute(
            select(SqlChatThread)
            .where(
                SqlChatThread.workspace_id == current_workspace_id(),
                SqlChatThread.root_message_id == thread_root_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if thread is not None and thread.brake_notice_at is not None:
            return None
        message = _insert_system_message(
            session, channel_id, thread_root_id, _BRAKE_BODY, _BRAKE_CODE
        )
        if thread is not None:
            thread.brake_notice_at = message.created_at
            thread.reply_count = thread.reply_count + 1
            thread.last_reply_at = message.created_at
            thread.updated_at = message.created_at
        return message
    if _channel_brake_posted(session, channel_id):
        return None
    return _insert_system_message(session, channel_id, None, _BRAKE_BODY, _BRAKE_CODE)


def _bot_streak(session: Session, channel_id: str, thread_root_id: str | None) -> int:
    """Count consecutive trailing bot messages in one channel timeline or thread.

    System rows are skipped. A human author resets the count. The scan looks
    at the newest twenty rows, which is enough to see five bots plus notices.
    """
    query = select(SqlChatMessage.author_kind).where(
        SqlChatMessage.workspace_id == current_workspace_id(),
        SqlChatMessage.channel_id == channel_id,
    )
    if thread_root_id is None:
        query = query.where(SqlChatMessage.thread_root_id.is_(None))
    else:
        query = query.where(SqlChatMessage.thread_root_id == thread_root_id)
    kinds = session.execute(
        query.order_by(SqlChatMessage.created_at.desc(), SqlChatMessage.id.desc()).limit(20)
    ).scalars()
    streak = 0
    for kind in kinds:
        if kind in _BOT_AUTHOR_KINDS:
            streak += 1
        elif kind in _HUMAN_AUTHOR_KINDS:
            break
    return streak


def _owned_account(session: Session, account_id: str, owner_id: str) -> SqlChatAgentAccount | None:
    """Return the account when *owner_id* owns it. A wrong owner looks missing."""
    return session.execute(
        select(SqlChatAgentAccount).where(
            SqlChatAgentAccount.workspace_id == current_workspace_id(),
            SqlChatAgentAccount.id == account_id,
            SqlChatAgentAccount.owner_id == owner_id,
        )
    ).scalar_one_or_none()


def _to_cursor(row: SqlChatRead) -> ReadCursor:
    """Copy a read-cursor row into a detached value."""
    return ReadCursor(
        channel_id=row.channel_id,
        principal_id=row.principal_id,
        thread_root_id=row.thread_root_id,
        last_read_at=row.last_read_at,
        last_read_id=row.last_read_id,
    )


def _cursor_covers(row: SqlChatRead, message: SqlChatMessage) -> bool:
    """Whether *row* already names *message* or a newer one."""
    if row.last_read_at is None or row.last_read_id is None:
        return False
    return (message.created_at, message.id) <= (row.last_read_at, row.last_read_id)


def _load_cursors(session: Session, principal_id: str) -> dict[tuple[str, str], tuple[int, str]]:
    """Map ``(channel_id, thread_root_id)`` to the newest read ``(created_at, id)``."""
    rows = session.execute(
        select(SqlChatRead).where(
            SqlChatRead.workspace_id == current_workspace_id(),
            SqlChatRead.principal_id == principal_id,
            SqlChatRead.last_read_at.is_not(None),
            SqlChatRead.last_read_id.is_not(None),
        )
    ).scalars()
    cursors: dict[tuple[str, str], tuple[int, str]] = {}
    for row in rows:
        if row.last_read_at is None or row.last_read_id is None:
            continue
        cursors[(row.channel_id, row.thread_root_id)] = (row.last_read_at, row.last_read_id)
    return cursors


def _message_scope(row: SqlChatMessage) -> str:
    """Read-cursor key for *row*. Top-level messages use an empty thread id."""
    return row.thread_root_id or ""


def _message_is_unread(
    row: SqlChatMessage, cursors: dict[tuple[str, str], tuple[int, str]]
) -> bool:
    """Whether *row* is strictly after the cursor for its channel or thread."""
    cursor = cursors.get((row.channel_id, _message_scope(row)))
    if cursor is None:
        return True
    return (row.created_at, row.id) > cursor


def _owner_channel_ids(session: Session, principal_id: str) -> set[str]:
    """Channel ids where the agent account's owner is also a member.

    An unknown or revoked account contributes no channels.
    """
    owner_id = session.execute(
        select(SqlChatAgentAccount.owner_id).where(
            SqlChatAgentAccount.workspace_id == current_workspace_id(),
            SqlChatAgentAccount.principal_id == principal_id,
            SqlChatAgentAccount.revoked_at.is_(None),
        )
    ).scalar_one_or_none()
    if not isinstance(owner_id, str):
        return set()
    return set(
        session.execute(
            select(SqlChatMember.channel_id).where(
                SqlChatMember.workspace_id == current_workspace_id(),
                SqlChatMember.principal_id == owner_id,
            )
        ).scalars()
    )


def _after_cursor(cursor: tuple[int, str] | None):
    """SQL predicate for messages strictly after *cursor*. ``None`` matches all."""
    if cursor is None:
        return None
    return _after_predicate(cursor)


def _collect_channel_unread(
    session: Session,
    principal_id: str,
    channel_id: str,
    cursors: dict[tuple[str, str], tuple[int, str]],
    consider: Callable[[SqlChatMessage, str], None],
) -> None:
    """Add this channel's unread mentions, DM rows, and thread replies.

    Each query starts at that scope's cursor, so already-read history is not
    scanned. Thread replies are limited to the thirty most recently updated
    threads the caller authored, joined, or was mentioned in.
    """
    channel_cursor = cursors.get((channel_id, ""))
    mention_query = (
        select(SqlChatMessage)
        .join(
            SqlChatMention,
            and_(
                SqlChatMention.workspace_id == SqlChatMessage.workspace_id,
                SqlChatMention.message_id == SqlChatMessage.id,
            ),
        )
        .where(
            SqlChatMention.workspace_id == current_workspace_id(),
            SqlChatMention.target_id == principal_id,
            SqlChatMessage.channel_id == channel_id,
        )
    )
    after = _after_cursor(channel_cursor)
    if after is not None:
        mention_query = mention_query.where(or_(SqlChatMessage.thread_root_id.is_not(None), after))
    for row in session.execute(
        mention_query.order_by(SqlChatMessage.created_at.asc(), SqlChatMessage.id.asc()).limit(
            _UNREAD_LIMIT
        )
    ).scalars():
        consider(row, "mention")

    kind = session.execute(
        select(SqlChatChannel.kind).where(
            SqlChatChannel.workspace_id == current_workspace_id(),
            SqlChatChannel.id == channel_id,
        )
    ).scalar_one_or_none()
    if kind == "dm":
        dm_query = select(SqlChatMessage).where(
            SqlChatMessage.workspace_id == current_workspace_id(),
            SqlChatMessage.channel_id == channel_id,
            SqlChatMessage.author_id != principal_id,
            SqlChatMessage.thread_root_id.is_(None),
        )
        if after is not None:
            dm_query = dm_query.where(after)
        for row in session.execute(
            dm_query.order_by(SqlChatMessage.created_at.asc(), SqlChatMessage.id.asc()).limit(
                _UNREAD_LIMIT
            )
        ).scalars():
            consider(row, "dm")
        dm_threads = session.execute(
            select(SqlChatThread.root_message_id)
            .where(
                SqlChatThread.workspace_id == current_workspace_id(),
                SqlChatThread.channel_id == channel_id,
            )
            .order_by(SqlChatThread.updated_at.desc())
            .limit(_THREAD_SCAN)
        ).scalars()
        for thread_id in dm_threads:
            _consider_replies(
                session, principal_id, channel_id, thread_id, cursors, consider, "dm"
            )

    # Agent wake is mentions plus DM rows. A thread reply wakes the account
    # only when it mentions that account (covered by the mention query).


def _consider_replies(
    session: Session,
    principal_id: str,
    channel_id: str,
    thread_id: str,
    cursors: dict[tuple[str, str], tuple[int, str]],
    consider: Callable[[SqlChatMessage, str], None],
    default_reason: str,
) -> None:
    """Add unread replies in one thread. A mention outranks *default_reason*."""
    reply_query = select(SqlChatMessage).where(
        SqlChatMessage.workspace_id == current_workspace_id(),
        SqlChatMessage.channel_id == channel_id,
        SqlChatMessage.thread_root_id == thread_id,
        SqlChatMessage.author_id != principal_id,
    )
    reply_after = _after_cursor(cursors.get((channel_id, thread_id)))
    if reply_after is not None:
        reply_query = reply_query.where(reply_after)
    for row in session.execute(
        reply_query.order_by(SqlChatMessage.created_at.asc(), SqlChatMessage.id.asc()).limit(
            _UNREAD_LIMIT
        )
    ).scalars():
        reason = (
            "mention" if _mentions_principal(session, row.id, principal_id) else default_reason
        )
        consider(row, reason)


def _mentions_principal(session: Session, message_id: str, principal_id: str) -> bool:
    """Whether *message_id* has a validated mention of *principal_id*."""
    found = session.execute(
        select(SqlChatMention.message_id).where(
            SqlChatMention.workspace_id == current_workspace_id(),
            SqlChatMention.message_id == message_id,
            SqlChatMention.target_id == principal_id,
        )
    ).first()
    return found is not None
