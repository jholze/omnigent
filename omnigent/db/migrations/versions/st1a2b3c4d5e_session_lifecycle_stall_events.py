"""session lifecycle outbox accepts stall events

Revision ID: st1a2b3c4d5e
Revises: ps1a2b3c4d5e
Create Date: 2026-10-31 00:00:00.000000

A quiet running turn records ``session.stalled`` (code 6) and the next
progress event records ``session.recovered`` (code 7). Downgrade deletes
those rows before the narrower check is restored, so the old constraint
can be applied.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "st1a2b3c4d5e"
down_revision: str | None = "ps1a2b3c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "session_lifecycle_outbox"
_CHECK = "ck_session_lifecycle_outbox_event_type"


def _replace_event_type_check(expression: str) -> None:
    """Replace the event-type check. SQLite rebuilds the table to do it."""
    sqlite = op.get_bind().dialect.name == "sqlite"
    with op.batch_alter_table(_TABLE, recreate="always" if sqlite else "auto") as batch_op:
        batch_op.drop_constraint(_CHECK, type_="check")
        batch_op.create_check_constraint(_CHECK, expression)


def upgrade() -> None:
    """Allow stall and recovery rows in the lifecycle outbox."""
    _replace_event_type_check("event_type IN (1, 2, 3, 4, 5, 6, 7)")


def downgrade() -> None:
    """Drop stall rows, then restore the previous event-type check."""
    op.execute(sa.text(f"DELETE FROM {_TABLE} WHERE event_type IN (6, 7)"))
    _replace_event_type_check("event_type IN (1, 2, 3, 4, 5)")
