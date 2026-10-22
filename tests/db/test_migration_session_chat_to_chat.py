"""Legacy session-chat lines move into a ``kind=session`` team-chat channel.

Revision dx1a2b3c4d5e already drops ``session_chat_messages``, so the test
recreates and seeds it before upgrading to the copy revision tc2a2b3c4d5e.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic import command

from omnigent.db.db_models import Uuid16
from omnigent.db.utils import _build_alembic_config, clear_engine_cache

# ps1a2b3c4d5e follows the drop revision dx1a2b3c4d5e, so the legacy table is gone.
_PARENT = "ps1a2b3c4d5e"
_COPY = "tc2a2b3c4d5e"
_LEGACY = "session_chat_messages"

_legacy = sa.Table(
    _LEGACY,
    sa.MetaData(),
    sa.Column("workspace_id", sa.BigInteger(), primary_key=True, server_default="0"),
    sa.Column("root_conversation_id", Uuid16(), primary_key=True),
    sa.Column("id", Uuid16(), primary_key=True),
    sa.Column("created_by", sa.String(length=128), nullable=True),
    sa.Column("created_at", sa.BigInteger(), nullable=False),
    sa.Column("body", sa.Text(), nullable=False),
)
_channels = sa.table(
    "chat_channels",
    sa.column("id", Uuid16()),
    sa.column("kind", sa.String(16)),
    sa.column("session_root_id", Uuid16()),
)
_messages = sa.table(
    "chat_messages",
    sa.column("id", Uuid16()),
    sa.column("channel_id", Uuid16()),
    sa.column("author_id", sa.String(160)),
    sa.column("created_at", sa.BigInteger()),
    sa.column("body", sa.Text()),
)


def _upgrade(uri: str, engine: sa.Engine, revision: str) -> None:
    config = _build_alembic_config(uri)
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, revision)


def _stamp(uri: str, engine: sa.Engine, revision: str) -> None:
    config = _build_alembic_config(uri)
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.stamp(config, revision)


def _seed_legacy_rows(engine: sa.Engine, root_id: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = [
        {
            "workspace_id": 0,
            "root_conversation_id": root_id,
            "id": uuid.uuid4().hex,
            "created_by": author,
            "created_at": 1_700_000_000_000_000 + offset,
            "body": body,
        }
        for offset, (author, body) in enumerate(
            [
                ("jens@bloopark.de", "I'll look at the test"),
                ("anna@bloopark.de", "Nur für Menschen"),
                ("jens@bloopark.de", "done"),
            ]
        )
    ]
    with engine.begin() as conn:
        _legacy.create(conn)
        conn.execute(_legacy.insert(), rows)
    return rows


def _session_messages(engine: sa.Engine, root_id: str) -> list[tuple[str, str, int, str]]:
    query = (
        sa.select(
            _messages.c.id,
            _messages.c.author_id,
            _messages.c.created_at,
            _messages.c.body,
        )
        .select_from(_messages.join(_channels, _messages.c.channel_id == _channels.c.id))
        .where(_channels.c.kind == "session", _channels.c.session_root_id == root_id)
        .order_by(_messages.c.created_at)
    )
    with engine.connect() as conn:
        return [tuple(row) for row in conn.execute(query).all()]


def _session_channel_count(engine: sa.Engine, root_id: str) -> int:
    query = (
        sa.select(sa.func.count())
        .select_from(_channels)
        .where(_channels.c.kind == "session", _channels.c.session_root_id == root_id)
    )
    with engine.connect() as conn:
        return conn.execute(query).scalar_one()


def test_ac_031a_legacy_rows_copy_once_into_session_channel(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'session_chat_to_chat.db'}"
    engine = sa.create_engine(uri)
    root_id = uuid.uuid4().hex

    _upgrade(uri, engine, _PARENT)
    assert _LEGACY not in sa.inspect(engine).get_table_names()
    seeded = _seed_legacy_rows(engine, root_id)
    expected = [(row["id"], row["created_by"], row["created_at"], row["body"]) for row in seeded]

    _upgrade(uri, engine, _COPY)
    assert _session_messages(engine, root_id) == expected
    assert _session_channel_count(engine, root_id) == 1

    # Re-run the copy revision over the already migrated rows.
    _stamp(uri, engine, _PARENT)
    _upgrade(uri, engine, _COPY)
    assert _session_messages(engine, root_id) == expected
    assert _session_channel_count(engine, root_id) == 1

    assert _LEGACY in sa.inspect(engine).get_table_names()

    engine.dispose()
    clear_engine_cache()
