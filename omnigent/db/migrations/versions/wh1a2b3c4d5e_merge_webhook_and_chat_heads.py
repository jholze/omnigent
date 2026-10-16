"""merge webhook and chat heads

Revision ID: wh1a2b3c4d5e
Revises: hm1a2b3c4d5e, mw1b2c3d4e5f
Create Date: 2026-10-16 00:00:00.000000

This revision joins the webhook head with the accounts-and-session-chat head
that is already on main so the migration graph has one head.
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "wh1a2b3c4d5e"
down_revision: str | tuple[str, ...] | None = ("hm1a2b3c4d5e", "mw1b2c3d4e5f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
