"""agent onboarding status and audit

Revision ID: ob1a2b3c4d5e
Revises: ps1a2b3c4d5e
Create Date: 2026-11-05 12:00:00.000000

Onboarding state for an agent account and one project. The audit table stores
who changed the status. Neither table stores tokens or rejected secret text.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "ob1a2b3c4d5e"
down_revision: str | None = "ps1a2b3c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the onboarding record and its audit log."""
    op.create_table(
        "agent_onboarding",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("account_id", Uuid16(), nullable=False),
        sa.Column("project_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("guide_version", sa.String(32), nullable=False),
        sa.Column("guide_hash", sa.String(64), nullable=False),
        sa.Column("answers_json", sa.Text(), nullable=True),
        sa.Column("answers_hash", sa.String(64), nullable=True),
        sa.Column("rework_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("findings_json", sa.Text(), nullable=False),
        sa.Column("owner_notices_json", sa.Text(), nullable=False),
        sa.Column("bot_notices_json", sa.Text(), nullable=False),
        sa.Column("review_json", sa.Text(), nullable=True),
        sa.Column("archive_json", sa.Text(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("previous_id", Uuid16(), nullable=True),
        sa.Column("opened_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("submitted_at", sa.BigInteger(), nullable=True),
        sa.Column("approved_by", sa.String(128), nullable=True),
        sa.Column("host_check", sa.String(16), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "uq_agent_onboarding_account_project",
        "agent_onboarding",
        ["workspace_id", "account_id", "project_id"],
        unique=True,
    )
    op.create_table(
        "agent_onboarding_audit",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("onboarding_id", Uuid16(), nullable=False),
        sa.Column("at", sa.BigInteger(), nullable=False),
        sa.Column("actor_kind", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(160), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("status_after", sa.String(32), nullable=False),
        sa.Column("guide_hash", sa.String(64), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_agent_onboarding_audit_record",
        "agent_onboarding_audit",
        ["workspace_id", "onboarding_id", "at"],
    )


def downgrade() -> None:
    """Drop the onboarding audit and record tables."""
    op.drop_index("ix_agent_onboarding_audit_record", table_name="agent_onboarding_audit")
    op.drop_table("agent_onboarding_audit")
    op.drop_index("uq_agent_onboarding_account_project", table_name="agent_onboarding")
    op.drop_table("agent_onboarding")
