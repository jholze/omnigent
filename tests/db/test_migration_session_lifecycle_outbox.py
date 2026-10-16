"""Migration ``mw1b2c3d4e5f`` adds the manager-webhook tables on SQLite.

An existing ``chat.db`` already at the previous head must gain the three
tables without rewriting ``agents`` or any other existing table.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config, clear_engine_cache, get_or_create_engine

_TABLES = (
    "session_lifecycle_cursors",
    "session_lifecycle_outbox",
    "session_elicitations",
)


def _names(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names())


def test_tables_exist_at_head(tmp_path: Path) -> None:
    engine = get_or_create_engine(f"sqlite:///{tmp_path / 'head.db'}")
    try:
        names = _names(engine)
        assert set(_TABLES) <= names
        assert "agents" in names
    finally:
        clear_engine_cache()


def test_upgrade_from_previous_head_keeps_existing_tables(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'existing.db'}"
    cfg = _build_alembic_config(uri)
    engine = sa.create_engine(uri)
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "mm1a2b3c4d5e")
        before = _names(engine)
        assert "agents" in before
        assert "session_lifecycle_outbox" not in before
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "mw1b2c3d4e5f")
        after = _names(engine)
        assert set(_TABLES) <= after
        assert "agents" in after
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "mm1a2b3c4d5e")
        downgraded = _names(engine)
        assert not (set(_TABLES) & downgraded)
        assert "agents" in downgraded
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
        assert set(_TABLES) <= _names(engine)
    finally:
        engine.dispose()
        clear_engine_cache()
