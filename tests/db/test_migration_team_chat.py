"""Tests for the team-chat migration chain (head td1a2b3c4d5e).

Uniqueness uses plain unique indexes rather than partial ones so every
dialect, MySQL included, behaves the same: rows with a NULL key never collide.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory

from omnigent.db.utils import _build_alembic_config, clear_engine_cache

_REVISION = "td1a2b3c4d5e"
_DOWN_REVISION = "wh1a2b3c4d5e"
_CHAT_TABLES = {
    "chat_channels",
    "chat_members",
    "chat_messages",
    "chat_threads",
    "chat_mentions",
    "chat_reads",
    "chat_agent_accounts",
    "chat_agent_outbox",
    "chat_user_prefs",
}
_CHANNEL_A = "a" * 32
_CHANNEL_B = "b" * 32
_CHANNEL_C = "c" * 32


def _upgrade(uri: str, engine: sa.Engine, revision: str) -> None:
    config = _build_alembic_config(uri)
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, revision)


def _downgrade(uri: str, engine: sa.Engine, revision: str) -> None:
    config = _build_alembic_config(uri)
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.downgrade(config, revision)


@pytest.fixture()
def migrated(tmp_path: Path) -> Iterator[tuple[str, sa.Engine]]:
    uri = f"sqlite:///{tmp_path / 'team_chat.db'}"
    engine = sa.create_engine(uri)
    _upgrade(uri, engine, _REVISION)
    yield uri, engine
    engine.dispose()
    clear_engine_cache()


def _insert_channel(engine: sa.Engine, channel_id: str, kind: str, dm_key: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO chat_channels (workspace_id, id, kind, dm_key, created_at) "
                "VALUES (0, :id, :kind, :dm_key, 1)"
            ),
            {"id": channel_id, "kind": kind, "dm_key": dm_key},
        )


def _insert_message(engine: sa.Engine, message_id: str, client_id: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO chat_messages "
                "(workspace_id, id, channel_id, author_id, created_at, body, client_id) "
                "VALUES (0, :id, :channel, 'user:anna', 1, 'x', :client_id)"
            ),
            {"id": message_id, "channel": _CHANNEL_A, "client_id": client_id},
        )


def _index_sql(engine: sa.Engine, name: str) -> str:
    with engine.connect() as conn:
        return conn.execute(
            sa.text("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = :name"),
            {"name": name},
        ).scalar_one()


def test_team_chat_stays_on_the_single_alembic_head() -> None:
    script = ScriptDirectory.from_config(_build_alembic_config("sqlite://"))
    heads = script.get_heads()

    assert len(heads) == 1
    chain = {rev.revision for rev in script.iterate_revisions(heads[0], "base")}
    assert _REVISION in chain


def test_upgrade_creates_all_chat_tables(migrated: tuple[str, sa.Engine]) -> None:
    _, engine = migrated

    assert set(sa.inspect(engine).get_table_names()) >= _CHAT_TABLES


def test_downgrade_drops_all_chat_tables(migrated: tuple[str, sa.Engine]) -> None:
    uri, engine = migrated

    _downgrade(uri, engine, _DOWN_REVISION)

    assert not _CHAT_TABLES & set(sa.inspect(engine).get_table_names())


def test_duplicate_dm_key_is_rejected(migrated: tuple[str, sa.Engine]) -> None:
    _, engine = migrated
    _insert_channel(engine, _CHANNEL_A, "dm", "user:anna|user:ben")

    with pytest.raises(sa.exc.IntegrityError):
        _insert_channel(engine, _CHANNEL_B, "dm", "user:anna|user:ben")


def test_null_dm_key_does_not_collide(migrated: tuple[str, sa.Engine]) -> None:
    _, engine = migrated
    _insert_channel(engine, _CHANNEL_A, "public", None)
    _insert_channel(engine, _CHANNEL_B, "public", None)
    _insert_channel(engine, _CHANNEL_C, "public", None)

    with engine.connect() as conn:
        count = conn.execute(sa.text("SELECT COUNT(*) FROM chat_channels")).scalar_one()
    assert count == 3


def test_duplicate_client_id_is_rejected(migrated: tuple[str, sa.Engine]) -> None:
    _, engine = migrated
    _insert_message(engine, _CHANNEL_A, "client-1")

    with pytest.raises(sa.exc.IntegrityError):
        _insert_message(engine, _CHANNEL_B, "client-1")


def test_null_client_id_does_not_collide(migrated: tuple[str, sa.Engine]) -> None:
    _, engine = migrated
    _insert_message(engine, _CHANNEL_A, None)
    _insert_message(engine, _CHANNEL_B, None)

    with engine.connect() as conn:
        count = conn.execute(sa.text("SELECT COUNT(*) FROM chat_messages")).scalar_one()
    assert count == 2


@pytest.mark.parametrize(
    "index_name",
    [
        "uq_chat_channels_workspace_dm_key",
        "uq_chat_messages_workspace_channel_author_client",
    ],
)
def test_unique_indexes_are_plain_not_partial(
    migrated: tuple[str, sa.Engine], index_name: str
) -> None:
    _, engine = migrated

    sql = _index_sql(engine, index_name)

    assert sql.upper().startswith("CREATE UNIQUE INDEX")
    assert "WHERE" not in sql.upper()
