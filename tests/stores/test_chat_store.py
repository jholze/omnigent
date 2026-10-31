"""Store tests for the team-chat persistence layer."""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from omnigent.db.db_models import (
    SqlChatAgentAccount,
    SqlChatChannel,
    SqlChatMention,
    SqlChatMessage,
)
from omnigent.db.utils import get_or_create_engine, now_epoch_us
from omnigent.stores.chat_store import (
    ChatChannel,
    DmStatus,
    PostStatus,
)
from omnigent.stores.chat_store.sqlalchemy_store import SqlAlchemyChatStore


@pytest.fixture()
def chat_store(db_uri: str) -> SqlAlchemyChatStore:
    """A chat store backed by the per-test SQLite database."""
    return SqlAlchemyChatStore(db_uri)


def _count_channels(db_uri: str) -> int:
    engine = get_or_create_engine(db_uri)
    with Session(engine) as session:
        return session.execute(select(func.count()).select_from(SqlChatChannel)).scalar_one()


def _mention_rows(db_uri: str) -> list[tuple[str, str]]:
    engine = get_or_create_engine(db_uri)
    with Session(engine) as session:
        rows = session.execute(
            select(SqlChatMention.target_kind, SqlChatMention.target_id).order_by(
                SqlChatMention.target_id
            )
        ).all()
        return [(kind, target) for kind, target in rows]


def _add_agent_account(db_uri: str, principal_id: str, owner_id: str) -> None:
    engine = get_or_create_engine(db_uri)
    with Session(engine) as session:
        session.add(
            SqlChatAgentAccount(
                id=uuid.uuid4().hex,
                principal_id=principal_id,
                owner_id=owner_id,
                display_name="Grok",
                handle="grok",
                token_hash=uuid.uuid4().hex,
                token_prefix="prefix1",
                secret_version=1,
                webhook_url="http://example.invalid",
                created_at=1,
            )
        )
        session.commit()


def test_ac_018a_replay_returns_original(
    chat_store: SqlAlchemyChatStore,
) -> None:
    channel = chat_store.bootstrap_channel("general", "anna@bloopark.de")
    first = chat_store.post_message(channel.id, "anna@bloopark.de", "Hallo", client_id="c-1")
    assert first.status is PostStatus.CREATED
    assert first.message is not None

    replay = chat_store.post_message(channel.id, "anna@bloopark.de", "Hallo", client_id="c-1")
    assert replay.status is PostStatus.REPLAYED
    assert replay.message is not None
    assert replay.message.id == first.message.id
    assert replay.message.body == "Hallo"


def test_ac_018b_body_conflict_writes_nothing(
    chat_store: SqlAlchemyChatStore,
) -> None:
    channel = chat_store.bootstrap_channel("general", "anna@bloopark.de")
    first = chat_store.post_message(channel.id, "anna@bloopark.de", "Hallo", client_id="c-1")
    assert first.message is not None

    conflict = chat_store.post_message(channel.id, "anna@bloopark.de", "Anders", client_id="c-1")
    assert conflict.status is PostStatus.BODY_CONFLICT
    assert conflict.message is None

    page = chat_store.list_messages(channel.id)
    assert [item.body for item in page.items] == ["Hallo"]


def test_ac_023a_reply_stored_in_thread(
    chat_store: SqlAlchemyChatStore,
) -> None:
    channel = chat_store.bootstrap_channel("general", "anna@bloopark.de")
    root = chat_store.post_message(channel.id, "anna@bloopark.de", "Frage")
    assert root.message is not None

    reply = chat_store.post_message(
        channel.id, "jens@bloopark.de", "Passt", thread_root_id=root.message.id
    )
    assert reply.status is PostStatus.CREATED
    assert reply.message is not None
    assert reply.message.thread_root_id == root.message.id

    thread = chat_store.get_thread(root.message.id)
    assert thread is not None
    assert thread.reply_count == 1

    replies = chat_store.list_messages(channel.id, thread_root_id=root.message.id)
    assert [item.body for item in replies.items] == ["Passt"]


def test_ac_024a_nested_thread_stores_nothing(
    chat_store: SqlAlchemyChatStore,
) -> None:
    channel = chat_store.bootstrap_channel("general", "anna@bloopark.de")
    root = chat_store.post_message(channel.id, "anna@bloopark.de", "Frage")
    assert root.message is not None
    reply = chat_store.post_message(
        channel.id, "jens@bloopark.de", "Passt", thread_root_id=root.message.id
    )
    assert reply.message is not None

    nested = chat_store.post_message(
        channel.id, "anna@bloopark.de", "Nochmal", thread_root_id=reply.message.id
    )
    assert nested.status is PostStatus.NESTED_THREAD
    assert nested.message is None

    thread = chat_store.get_thread(root.message.id)
    assert thread is not None
    assert thread.reply_count == 1


def test_ac_024b_thread_root_from_other_channel(
    chat_store: SqlAlchemyChatStore,
) -> None:
    general = chat_store.bootstrap_channel("general", "jens@bloopark.de")
    billing = chat_store.bootstrap_channel("billing", "jens@bloopark.de")
    root = chat_store.post_message(billing.id, "jens@bloopark.de", "Rechnung")
    assert root.message is not None

    result = chat_store.post_message(
        general.id, "jens@bloopark.de", "Passt", thread_root_id=root.message.id
    )
    assert result.status is PostStatus.THREAD_ROOT_NOT_FOUND
    assert result.message is None

    page = chat_store.list_messages(general.id)
    assert page.items == ()


def test_ac_025a_reply_count_last_reply_and_participants(
    chat_store: SqlAlchemyChatStore,
) -> None:
    channel = chat_store.bootstrap_channel("general", "anna@bloopark.de")
    chat_store.add_member(channel.id, "jens@bloopark.de")
    chat_store.add_member(channel.id, "bob@bloopark.de")
    root = chat_store.post_message(channel.id, "anna@bloopark.de", "Frage")
    assert root.message is not None

    jens = chat_store.post_message(
        channel.id, "jens@bloopark.de", "Jens", thread_root_id=root.message.id
    )
    assert jens.message is not None
    bob = chat_store.post_message(
        channel.id, "bob@bloopark.de", "Bob", thread_root_id=root.message.id
    )
    assert bob.message is not None

    thread = chat_store.get_thread(root.message.id)
    assert thread is not None
    assert thread.reply_count == 2
    assert thread.last_reply_at == bob.message.created_at
    assert thread.participants == ("bob@bloopark.de", "jens@bloopark.de")


def test_ac_025b_participants_capped_at_five(
    chat_store: SqlAlchemyChatStore,
) -> None:
    channel = chat_store.bootstrap_channel("general", "anna@bloopark.de")
    root = chat_store.post_message(channel.id, "anna@bloopark.de", "Frage")
    assert root.message is not None

    repliers = [f"u{i}@bloopark.de" for i in range(7)]
    for principal in repliers:
        chat_store.add_member(channel.id, principal)
        chat_store.post_message(channel.id, principal, "Antwort", thread_root_id=root.message.id)

    thread = chat_store.get_thread(root.message.id)
    assert thread is not None
    assert thread.reply_count == 7
    assert thread.participants == tuple(reversed(repliers[-5:]))


def test_ac_027a_valid_user_mention_creates_row(
    chat_store: SqlAlchemyChatStore, db_uri: str
) -> None:
    channel = chat_store.bootstrap_channel("general", "jens@bloopark.de")
    chat_store.add_member(channel.id, "anna@bloopark.de")

    result = chat_store.post_message(
        channel.id, "jens@bloopark.de", "<@user:anna@bloopark.de> bitte prüfen"
    )
    assert result.status is PostStatus.CREATED

    rows = _mention_rows(db_uri)
    assert ("user", "anna@bloopark.de") in rows


def test_ac_027b_invalid_user_mention_stays_literal(
    chat_store: SqlAlchemyChatStore, db_uri: str
) -> None:
    channel = chat_store.bootstrap_channel("general", "jens@bloopark.de")

    result = chat_store.post_message(channel.id, "jens@bloopark.de", "<@user:nobody@x.de> hallo")
    assert result.status is PostStatus.CREATED
    assert result.message is not None
    assert result.message.body == "<@user:nobody@x.de> hallo"

    assert _mention_rows(db_uri) == []


def test_user_mention_of_general_member_from_another_channel(
    chat_store: SqlAlchemyChatStore, db_uri: str
) -> None:
    chat_store.bootstrap_channel("general", "anna@bloopark.de")
    billing = chat_store.bootstrap_channel("billing", "jens@bloopark.de")

    result = chat_store.post_message(
        billing.id, "jens@bloopark.de", "<@user:anna@bloopark.de> bitte prüfen"
    )
    assert result.status is PostStatus.CREATED

    assert _mention_rows(db_uri) == [("user", "anna@bloopark.de")]


def test_agent_prefixed_id_is_not_a_user_mention(
    chat_store: SqlAlchemyChatStore, db_uri: str
) -> None:
    channel = chat_store.bootstrap_channel("general", "jens@bloopark.de")
    chat_store.add_member(channel.id, "agent:111")

    result = chat_store.post_message(channel.id, "jens@bloopark.de", "<@user:agent:111> hallo")
    assert result.status is PostStatus.CREATED
    assert result.message is not None
    assert result.message.body == "<@user:agent:111> hallo"

    assert _mention_rows(db_uri) == []


def test_agent_account_mention_validates_account(
    chat_store: SqlAlchemyChatStore, db_uri: str
) -> None:
    channel = chat_store.bootstrap_channel("general", "jens@bloopark.de")
    _add_agent_account(db_uri, "agent:111", "jens@bloopark.de")

    chat_store.post_message(
        channel.id, "jens@bloopark.de", "<@agent_account:agent:111> fass zusammen"
    )
    chat_store.post_message(channel.id, "jens@bloopark.de", "<@agent_account:agent:999> hallo")

    rows = _mention_rows(db_uri)
    assert ("agent_account", "agent:111") in rows
    assert ("agent_account", "agent:999") not in rows


def test_omnigent_agent_mention_validates_handle(
    chat_store: SqlAlchemyChatStore, db_uri: str
) -> None:
    channel = chat_store.bootstrap_channel("general", "jens@bloopark.de")

    chat_store.post_message(
        channel.id,
        "jens@bloopark.de",
        "<@omnigent_agent:coder> mach was",
        omnigent_agent_handles={"coder"},
    )
    chat_store.post_message(
        channel.id,
        "jens@bloopark.de",
        "<@omnigent_agent:ghost> mach was",
        omnigent_agent_handles={"coder"},
    )

    rows = _mention_rows(db_uri)
    assert ("omnigent_agent", "coder") in rows
    assert ("omnigent_agent", "ghost") not in rows


def test_paging_ascending_cursor(
    chat_store: SqlAlchemyChatStore,
) -> None:
    channel = chat_store.bootstrap_channel("general", "anna@bloopark.de")
    for index in range(6):
        chat_store.post_message(channel.id, "anna@bloopark.de", f"m{index}")

    first = chat_store.list_messages(channel.id, limit=2)
    assert [item.body for item in first.items] == ["m0", "m1"]
    assert first.has_more is True
    assert first.next_after is not None

    second = chat_store.list_messages(channel.id, limit=2, after=first.next_after)
    assert [item.body for item in second.items] == ["m2", "m3"]

    third = chat_store.list_messages(channel.id, limit=2, after=second.next_after)
    assert [item.body for item in third.items] == ["m4", "m5"]
    assert third.has_more is False

    all_items = [*first.items, *second.items, *third.items]
    cursor = (-1, "")
    for item in all_items:
        assert (item.created_at, item.id) > cursor
        cursor = (item.created_at, item.id)


def test_paging_tail_is_bounded(
    chat_store: SqlAlchemyChatStore,
) -> None:
    channel = chat_store.bootstrap_channel("general", "anna@bloopark.de")
    for index in range(10):
        chat_store.post_message(channel.id, "anna@bloopark.de", f"m{index}")

    tail = chat_store.list_messages(channel.id, tail=3)
    assert [item.body for item in tail.items] == ["m7", "m8", "m9"]
    assert tail.has_more is False
    assert tail.has_older is True


_DAY_US = 24 * 60 * 60 * 1_000_000
_ZERO_MESSAGE_ID = "0" * 32


def test_ac_083a_changes_cursor_older_than_7_days_resets(
    chat_store: SqlAlchemyChatStore,
) -> None:
    channel = chat_store.bootstrap_channel("general", "jens@bloopark.de")
    chat_store.post_message(channel.id, "jens@bloopark.de", "pending")

    page = chat_store.list_changes(
        "jens@bloopark.de", after=(now_epoch_us() - 8 * _DAY_US, _ZERO_MESSAGE_ID)
    )

    assert page.reset is True
    assert page.items == ()
    assert page.next_after is None


def test_ac_083a_recent_changes_cursor_returns_pending_message(
    chat_store: SqlAlchemyChatStore,
) -> None:
    channel = chat_store.bootstrap_channel("general", "jens@bloopark.de")
    cursor = (now_epoch_us() - 60_000_000, _ZERO_MESSAGE_ID)
    posted = chat_store.post_message(channel.id, "jens@bloopark.de", "pending")
    assert posted.message is not None

    page = chat_store.list_changes("jens@bloopark.de", after=cursor)

    assert page.reset is False
    assert [item.id for item in page.items] == [posted.message.id]
    assert page.next_after == (posted.message.created_at, posted.message.id)


def test_ac_083a_more_than_1000_pending_changes_resets(
    chat_store: SqlAlchemyChatStore,
) -> None:
    channel = chat_store.bootstrap_channel("general", "jens@bloopark.de")
    for index in range(1001):
        chat_store.post_message(channel.id, "jens@bloopark.de", f"m{index}")

    page = chat_store.list_changes("jens@bloopark.de")

    assert page.reset is True
    assert len(page.items) == 1000


def test_dm_reuses_existing_set(chat_store: SqlAlchemyChatStore) -> None:
    first = chat_store.get_or_create_dm(["anna@bloopark.de", "jens@bloopark.de"])
    assert first.status is DmStatus.CREATED
    assert first.channel is not None
    assert first.channel.kind == "dm"

    again = chat_store.get_or_create_dm(["jens@bloopark.de", "anna@bloopark.de"])
    assert again.status is DmStatus.EXISTING
    assert again.channel is not None
    assert again.channel.id == first.channel.id

    different = chat_store.get_or_create_dm(["anna@bloopark.de", "bob@bloopark.de"])
    assert different.status is DmStatus.CREATED
    assert different.channel is not None
    assert different.channel.id != first.channel.id


def test_dm_agent_owner_required(chat_store: SqlAlchemyChatStore, db_uri: str) -> None:
    _add_agent_account(db_uri, "agent:111", "jens@bloopark.de")

    result = chat_store.get_or_create_dm(["anna@bloopark.de", "agent:111"])
    assert result.status is DmStatus.AGENT_OWNER_REQUIRED
    assert result.channel is None
    assert _count_channels(db_uri) == 0

    allowed = chat_store.get_or_create_dm(["jens@bloopark.de", "agent:111"])
    assert allowed.status is DmStatus.CREATED


def test_bootstrap_general_concurrent(chat_store: SqlAlchemyChatStore, db_uri: str) -> None:
    errors: list[BaseException] = []
    results: list[ChatChannel] = []
    lock = threading.Lock()

    def boot(principal: str) -> None:
        try:
            channel = chat_store.bootstrap_channel("general", principal)
            with lock:
                results.append(channel)
        except BaseException as exc:  # surfaced in the assertion
            errors.append(exc)

    threads = [threading.Thread(target=boot, args=(f"u{i}@bloopark.de",)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert _count_channels(db_uri) == 1
    assert {channel.id for channel in results} == {results[0].id}


def _set_created_at(db_uri: str, message_id: str, created_at: int) -> None:
    engine = get_or_create_engine(db_uri)
    with Session(engine) as session:
        session.execute(
            update(SqlChatMessage)
            .where(SqlChatMessage.id == message_id)
            .values(created_at=created_at)
        )
        session.commit()


def test_channel_unread_counts_until_includes_boundary_and_excludes_later(
    chat_store: SqlAlchemyChatStore, db_uri: str
) -> None:
    channel = chat_store.bootstrap_channel("general", "anna@bloopark.de")
    at_cut = chat_store.post_message(channel.id, "anna@bloopark.de", "at cut")
    after_cut = chat_store.post_message(channel.id, "anna@bloopark.de", "after cut")
    assert at_cut.message is not None and after_cut.message is not None
    until = now_epoch_us()
    _set_created_at(db_uri, at_cut.message.id, until)
    _set_created_at(db_uri, after_cut.message.id, until + 1)

    counts = chat_store.channel_unread_counts("bob@bloopark.de", [channel.id], until=until)

    assert counts[channel.id] == (1, 0)
