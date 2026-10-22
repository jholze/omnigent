"""The agent-account project allow-list migration is reversible (NFR-035)."""

from __future__ import annotations

import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory

from omnigent.db.utils import _build_alembic_config, clear_engine_cache

_REVISION = "ps1a2b3c4d5e"
_PARENT = "dx1a2b3c4d5e"
_TABLE = "chat_agent_accounts"


def _migrate(uri: str, engine: sa.Engine, step, revision: str) -> None:
    config = _build_alembic_config(uri)
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        step(config, revision)


def _schema(engine: sa.Engine) -> tuple[dict[str, set[str]], set[str]]:
    """Columns of every table, plus the account table's index names."""
    inspector = sa.inspect(engine)
    columns = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in inspector.get_table_names()
    }
    indexes = {index["name"] for index in inspector.get_indexes(_TABLE)}
    return columns, indexes


def _scopes(engine: sa.Engine) -> list[tuple[str, object]]:
    with engine.connect() as conn:
        rows = conn.execute(sa.text(f"SELECT handle, project_ids FROM {_TABLE} ORDER BY handle"))
        return [tuple(row) for row in rows]


def test_project_scope_revision_follows_the_session_lines_drop() -> None:
    script = ScriptDirectory.from_config(_build_alembic_config("sqlite://"))

    assert script.get_revision(_REVISION).down_revision == _PARENT


def test_ac_035a_up_down_up_keeps_existing_accounts_unscoped(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'agent_project_scope.db'}"
    engine = sa.create_engine(uri)
    _migrate(uri, engine, command.upgrade, _PARENT)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                f"INSERT INTO {_TABLE} (workspace_id, id, principal_id, owner_id, display_name, "
                "handle, token_hash, token_prefix, secret_version, webhook_url, created_at) "
                "VALUES (0, :id, :principal, 'jens@bloopark.de', 'PM - FT Max', 'pm-ft-max', "
                "'hash', 'ogc_abcd', 1, '', 1)"
            ),
            {"id": uuid.uuid4().bytes, "principal": f"agent:{uuid.uuid4().hex}"},
        )
    before = _schema(engine)

    _migrate(uri, engine, command.upgrade, _REVISION)
    assert "project_ids" in _schema(engine)[0][_TABLE]
    assert _scopes(engine) == [("pm-ft-max", None)]

    _migrate(uri, engine, command.downgrade, _PARENT)
    assert _schema(engine) == before
    with engine.connect() as conn:
        assert conn.execute(sa.text(f"SELECT handle FROM {_TABLE}")).scalars().all() == [
            "pm-ft-max"
        ]

    _migrate(uri, engine, command.upgrade, _REVISION)
    assert _scopes(engine) == [("pm-ft-max", None)]

    engine.dispose()
    clear_engine_cache()
