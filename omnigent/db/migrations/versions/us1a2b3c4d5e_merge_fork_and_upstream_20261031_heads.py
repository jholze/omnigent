"""merge fork and upstream heads (upstream sync 2026-10-31)

Revision ID: us1a2b3c4d5e
Revises: ps1a2b3c4d5e, d0a1f879a01d
Create Date: 2026-10-31 00:00:00.000000

The fork head ``ps1a2b3c4d5e`` (agent-account project scope) and the
upstream head ``d0a1f879a01d`` (scheduled task session mode) both descend
from ``mm1a2b3c4d5e``. The branches touch disjoint tables, so this empty
revision joins them and the graph has one head again. Upstream files stay
unedited so later syncs do not conflict on them.
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "us1a2b3c4d5e"
down_revision: str | tuple[str, ...] | None = ("ps1a2b3c4d5e", "d0a1f879a01d")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
