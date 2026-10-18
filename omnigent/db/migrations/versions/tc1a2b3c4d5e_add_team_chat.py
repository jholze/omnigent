"""add team chat tables

Revision ID: tc1a2b3c4d5e
Revises: wh1a2b3c4d5e
Create Date: 2026-10-17 00:00:00.000000

Creates the R0 and R1 team-chat tables in a single migration so the schema
only gains one head. Tables follow 05-design.md §Datenmodell: no foreign keys
(Rule R032), ids as ``Uuid16`` (bare 32-char hex), and all timestamps as epoch
microseconds (``BigInteger``). The earlier per-session human-lines table is
not part of this migration.

Partial indexes from the design are expressed as plain unique indexes where a
nullable column already scopes the predicate (``name`` is NULL for dm/session,
``dm_key``/``session_root_id``/``client_id``/``session_id`` are NULL when
unset), keeping every dialect — including MySQL, which has no partial indexes —
behaviourally identical.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "tc1a2b3c4d5e"
down_revision: str | None = "wh1a2b3c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the team-chat tables and their indexes."""
    op.create_table(
        "chat_channels",
        sa.Column(
            "workspace_id",
            sa.BigInteger(),
            primary_key=True,
            nullable=False,
            server_default="0",
        ),
        sa.Column("id", Uuid16(), primary_key=True, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(64), nullable=True),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("project_name", sa.String(256), nullable=True),
        sa.Column("visibility", sa.String(16), nullable=False, server_default="public"),
        sa.Column("dm_key", sa.String(64), nullable=True),
        sa.Column("session_root_id", Uuid16(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("archived_at", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "uq_chat_channels_workspace_name",
        "chat_channels",
        ["workspace_id", "name"],
        unique=True,
    )
    op.create_index(
        "uq_chat_channels_workspace_dm_key",
        "chat_channels",
        ["workspace_id", "dm_key"],
        unique=True,
    )
    op.create_index(
        "uq_chat_channels_workspace_session_root_id",
        "chat_channels",
        ["workspace_id", "session_root_id"],
        unique=True,
    )

    op.create_table(
        "chat_members",
        sa.Column(
            "workspace_id",
            sa.BigInteger(),
            primary_key=True,
            nullable=False,
            server_default="0",
        ),
        sa.Column("channel_id", Uuid16(), primary_key=True, nullable=False),
        sa.Column("principal_id", sa.String(160), primary_key=True, nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="member"),
        sa.Column("joined_at", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "ix_chat_members_workspace_principal",
        "chat_members",
        ["workspace_id", "principal_id"],
        unique=False,
    )

    op.create_table(
        "chat_messages",
        sa.Column(
            "workspace_id",
            sa.BigInteger(),
            primary_key=True,
            nullable=False,
            server_default="0",
        ),
        sa.Column("id", Uuid16(), primary_key=True, nullable=False),
        sa.Column("channel_id", Uuid16(), nullable=False),
        sa.Column("thread_root_id", Uuid16(), nullable=True),
        sa.Column("author_id", sa.String(160), nullable=False),
        sa.Column("author_kind", sa.String(16), nullable=False, server_default="user"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("client_id", sa.String(64), nullable=True),
        sa.Column("forward_state", sa.String(16), nullable=True),
        sa.Column("session_ref", Uuid16(), nullable=True),
        sa.Column("response_ref", sa.String(128), nullable=True),
        sa.Column("system_code", sa.String(64), nullable=True),
        sa.Column("edited_at", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.BigInteger(), nullable=True),
        sa.Column("deleted_by", sa.String(128), nullable=True),
    )
    op.create_index(
        "ix_chat_messages_workspace_channel_thread_created",
        "chat_messages",
        ["workspace_id", "channel_id", "thread_root_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "uq_chat_messages_workspace_channel_author_client",
        "chat_messages",
        ["workspace_id", "channel_id", "author_id", "client_id"],
        unique=True,
    )
    op.create_index(
        "ix_chat_messages_workspace_created",
        "chat_messages",
        ["workspace_id", "created_at", "id"],
        unique=False,
    )

    op.create_table(
        "chat_threads",
        sa.Column(
            "workspace_id",
            sa.BigInteger(),
            primary_key=True,
            nullable=False,
            server_default="0",
        ),
        sa.Column("root_message_id", Uuid16(), primary_key=True, nullable=False),
        sa.Column("channel_id", Uuid16(), nullable=False),
        sa.Column("reply_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_reply_at", sa.BigInteger(), nullable=True),
        sa.Column("participants", sa.String(1024), nullable=False, server_default="[]"),
        sa.Column("bot_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("brake_notice_at", sa.BigInteger(), nullable=True),
        sa.Column("session_id", Uuid16(), nullable=True),
        sa.Column("agent_handle", sa.String(64), nullable=True),
        sa.Column("agent_id", Uuid16(), nullable=True),
        sa.Column("agent_state", sa.String(16), nullable=False, server_default="none"),
        sa.Column("caller_id", sa.String(128), nullable=True),
        sa.Column("start_requested_at", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "uq_chat_threads_workspace_session",
        "chat_threads",
        ["workspace_id", "session_id"],
        unique=True,
    )
    op.create_index(
        "ix_chat_threads_workspace_channel_updated",
        "chat_threads",
        ["workspace_id", "channel_id", "updated_at"],
        unique=False,
    )

    op.create_table(
        "chat_mentions",
        sa.Column(
            "workspace_id",
            sa.BigInteger(),
            primary_key=True,
            nullable=False,
            server_default="0",
        ),
        sa.Column("message_id", Uuid16(), primary_key=True, nullable=False),
        sa.Column("channel_id", Uuid16(), nullable=False),
        sa.Column("target_kind", sa.String(16), nullable=False),
        sa.Column("target_id", sa.String(160), primary_key=True, nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "ix_chat_mentions_workspace_target_created",
        "chat_mentions",
        ["workspace_id", "target_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "chat_reads",
        sa.Column(
            "workspace_id",
            sa.BigInteger(),
            primary_key=True,
            nullable=False,
            server_default="0",
        ),
        sa.Column("channel_id", Uuid16(), primary_key=True, nullable=False),
        sa.Column("principal_id", sa.String(160), primary_key=True, nullable=False),
        sa.Column(
            "thread_root_id",
            sa.String(32),
            primary_key=True,
            nullable=False,
            server_default="",
        ),
        sa.Column("last_read_at", sa.BigInteger(), nullable=True),
        sa.Column("last_read_id", Uuid16(), nullable=True),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )

    op.create_table(
        "chat_agent_accounts",
        sa.Column(
            "workspace_id",
            sa.BigInteger(),
            primary_key=True,
            nullable=False,
            server_default="0",
        ),
        sa.Column("id", Uuid16(), primary_key=True, nullable=False),
        sa.Column("principal_id", sa.String(160), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(64), nullable=False),
        sa.Column("handle", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("token_prefix", sa.String(8), nullable=False),
        sa.Column("secret_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("webhook_url", sa.String(2048), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("last_seen_at", sa.BigInteger(), nullable=True),
        sa.Column("revoked_at", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "uq_chat_agent_accounts_workspace_handle",
        "chat_agent_accounts",
        ["workspace_id", "handle"],
        unique=True,
    )
    op.create_index(
        "uq_chat_agent_accounts_token_hash",
        "chat_agent_accounts",
        ["token_hash"],
        unique=True,
    )

    op.create_table(
        "chat_agent_outbox",
        sa.Column(
            "workspace_id",
            sa.BigInteger(),
            primary_key=True,
            nullable=False,
            server_default="0",
        ),
        sa.Column("event_id", Uuid16(), primary_key=True, nullable=False),
        sa.Column("account_id", Uuid16(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        # CompressedText → LargeBinary; JSON without any message body (NFR-014).
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.BigInteger(), nullable=False),
        sa.Column("lease_until", sa.BigInteger(), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "ix_chat_agent_outbox_status_next_attempt",
        "chat_agent_outbox",
        ["status", "next_attempt_at"],
        unique=False,
    )
    op.create_index(
        "ix_chat_agent_outbox_workspace_account_sequence",
        "chat_agent_outbox",
        ["workspace_id", "account_id", "sequence"],
        unique=False,
    )

    op.create_table(
        "chat_user_prefs",
        sa.Column(
            "workspace_id",
            sa.BigInteger(),
            primary_key=True,
            nullable=False,
            server_default="0",
        ),
        sa.Column("principal_id", sa.String(160), primary_key=True, nullable=False),
        sa.Column("default_project_id", Uuid16(), nullable=True),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )


def downgrade() -> None:
    """Drop the team-chat tables and their indexes."""
    op.drop_table("chat_user_prefs")

    op.drop_index(
        "ix_chat_agent_outbox_workspace_account_sequence",
        table_name="chat_agent_outbox",
    )
    op.drop_index(
        "ix_chat_agent_outbox_status_next_attempt",
        table_name="chat_agent_outbox",
    )
    op.drop_table("chat_agent_outbox")

    op.drop_index("uq_chat_agent_accounts_token_hash", table_name="chat_agent_accounts")
    op.drop_index("uq_chat_agent_accounts_workspace_handle", table_name="chat_agent_accounts")
    op.drop_table("chat_agent_accounts")

    op.drop_table("chat_reads")

    op.drop_index("ix_chat_mentions_workspace_target_created", table_name="chat_mentions")
    op.drop_table("chat_mentions")

    op.drop_index("ix_chat_threads_workspace_channel_updated", table_name="chat_threads")
    op.drop_index("uq_chat_threads_workspace_session", table_name="chat_threads")
    op.drop_table("chat_threads")

    op.drop_index("ix_chat_messages_workspace_created", table_name="chat_messages")
    op.drop_index("uq_chat_messages_workspace_channel_author_client", table_name="chat_messages")
    op.drop_index("ix_chat_messages_workspace_channel_thread_created", table_name="chat_messages")
    op.drop_table("chat_messages")

    op.drop_index("ix_chat_members_workspace_principal", table_name="chat_members")
    op.drop_table("chat_members")

    op.drop_index("uq_chat_channels_workspace_session_root_id", table_name="chat_channels")
    op.drop_index("uq_chat_channels_workspace_dm_key", table_name="chat_channels")
    op.drop_index("uq_chat_channels_workspace_name", table_name="chat_channels")
    op.drop_table("chat_channels")
