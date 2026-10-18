"""chat thread records the bot that started it

Revision ID: td1a2b3c4d5e
Revises: tc1a2b3c4d5e
Create Date: 2026-10-17 20:11:00.000000

An agent account may start an Omnigent session by mentioning the project
agent. The session owner is the human; this column stores the initiator.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "td1a2b3c4d5e"
down_revision: str | None = "tc1a2b3c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the bot initiator column. Existing threads stay human-started."""
    op.add_column(
        "chat_threads",
        sa.Column("started_by_agent_account_id", Uuid16(), nullable=True),
    )


def downgrade() -> None:
    """Drop the bot initiator column."""
    with op.batch_alter_table("chat_threads") as batch_op:
        batch_op.drop_column("started_by_agent_account_id")
