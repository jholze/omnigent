"""The per-session human-lines table is removed at the current head.

The drop revision follows td1a2b3c4d5e, the bot-initiator column.
Downgrade one step recreates the table, so the chain stays a single head.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory

from omnigent.db.utils import _build_alembic_config, clear_engine_cache

_HEAD = "dx1a2b3c4d5e"
# The agent-account project allow-list revises the drop and is the current head.
_ALEMBIC_HEAD = "ps1a2b3c4d5e"
_PARENT = "td1a2b3c4d5e"
_TABLE = "session_chat_messages"


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


def test_drop_is_the_single_alembic_head() -> None:
    script = ScriptDirectory.from_config(_build_alembic_config("sqlite://"))

    assert script.get_heads() == [_ALEMBIC_HEAD]


def test_upgrade_drops_table_and_downgrade_recreates_it(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'drop_session_lines.db'}"
    engine = sa.create_engine(uri)

    _upgrade(uri, engine, _PARENT)
    assert _TABLE in sa.inspect(engine).get_table_names()

    _upgrade(uri, engine, _HEAD)
    assert _TABLE not in sa.inspect(engine).get_table_names()

    _downgrade(uri, engine, _PARENT)
    inspector = sa.inspect(engine)
    assert _TABLE in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns(_TABLE)}
    assert columns == {
        "workspace_id",
        "root_conversation_id",
        "id",
        "created_by",
        "created_at",
        "body",
    }

    engine.dispose()
    clear_engine_cache()
