"""merge fork and upstream heads (upstream sync 2026-11-18)

Revision ID: us1a2b3c4d5e
Revises: ob1a2b3c4d5e, ffeec66eadb5
Create Date: 2026-11-18 00:00:00.000000

The fork head (agent onboarding, ``ob1a2b3c4d5e``) and the upstream head
(memories table, ``ffeec66eadb5``) both descend from the last synced
upstream revision. This revision joins them so the graph has one head.
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "us1a2b3c4d5e"
down_revision: str | tuple[str, ...] | None = ("ob1a2b3c4d5e", "ffeec66eadb5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
