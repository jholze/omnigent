"""drop session chat messages

Revision ID: dx1a2b3c4d5e
Revises: td1a2b3c4d5e
Create Date: 2026-10-18 00:00:00.000000

The per-session human chat column is gone. Rows already stored on the
owner's database are dropped. Downgrade recreates the empty table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "dx1a2b3c4d5e"
down_revision: str | None = "td1a2b3c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "session_chat_messages"
_INDEX = "ix_session_chat_messages_root_created"


def upgrade() -> None:
    """Drop the per-session human-lines table."""
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)


def downgrade() -> None:
    """Recreate the per-session human-lines table and its read index."""
    op.create_table(
        _TABLE,
        sa.Column(
            "workspace_id",
            sa.BigInteger(),
            primary_key=True,
            nullable=False,
            server_default="0",
        ),
        sa.Column("root_conversation_id", Uuid16(), primary_key=True, nullable=False),
        sa.Column("id", Uuid16(), primary_key=True, nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
    )
    op.create_index(
        _INDEX,
        _TABLE,
        ["workspace_id", "root_conversation_id", "created_at", "id"],
        unique=False,
    )
