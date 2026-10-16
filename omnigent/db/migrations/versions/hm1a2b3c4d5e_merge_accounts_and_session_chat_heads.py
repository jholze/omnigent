"""merge accounts and session chat heads

Revision ID: hm1a2b3c4d5e
Revises: nn1a2b3c4d5e, sc1a2b3c4d5e
Create Date: 2026-10-16 00:00:00.000000

Both heads revise ``mm1a2b3c4d5e``. This revision joins the accounts head
from main with the session-chat head so the migration graph has one head.
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "hm1a2b3c4d5e"
down_revision: str | tuple[str, ...] | None = ("nn1a2b3c4d5e", "sc1a2b3c4d5e")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
