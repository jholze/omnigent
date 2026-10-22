"""agent account project allow-list

Revision ID: ps1a2b3c4d5e
Revises: dx1a2b3c4d5e
Create Date: 2026-10-22 00:00:00.000000

An agent account may carry a JSON list of allowed project ids. Existing
accounts keep NULL, which stays unscoped. Downgrade drops only that column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ps1a2b3c4d5e"
down_revision: str | None = "dx1a2b3c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "chat_agent_accounts"
_COLUMN = "project_ids"


def upgrade() -> None:
    """Add the nullable allow-list column. Existing rows stay unscoped."""
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the allow-list column."""
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column(_COLUMN)
