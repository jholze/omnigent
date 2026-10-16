"""add google identity links and bind nonces

Revision ID: nn1a2b3c4d5e
Revises: mm1a2b3c4d5e
Create Date: 2026-10-13 00:00:00.000000

Accounts-mode Google sign-in stores one link per Google subject and a
single-use nonce for the login-page bind cookie. Older code ignores
both tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "nn1a2b3c4d5e"
down_revision: str | None = "mm1a2b3c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "google_identity_links",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("issuer", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "issuer", "subject"),
        sa.UniqueConstraint("workspace_id", "email", name="uq_google_identity_links_email"),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_google_identity_links_user"),
    )
    op.create_table(
        "google_bind_nonces",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("jti", sa.String(64), nullable=False),
        sa.Column("issuer", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "jti"),
    )


def downgrade() -> None:
    op.drop_table("google_bind_nonces")
    op.drop_table("google_identity_links")
