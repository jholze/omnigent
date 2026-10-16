"""add session chat messages

Revision ID: sc1a2b3c4d5e
Revises: mm1a2b3c4d5e
Create Date: 2026-10-15 00:00:00.000000

Lines people in one shared session send to each other. Stored on the root
conversation so a sub-agent view reads the same thread. The body is plain
text; the application caps it at 2000 characters.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "sc1a2b3c4d5e"
down_revision: str | None = "mm1a2b3c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "session_chat_messages"
_INDEX = "ix_session_chat_messages_root_created"


def upgrade() -> None:
    """Create the session chat table and its read index."""
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


def downgrade() -> None:
    """Drop the session chat table."""
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)
