"""migrate session chat messages into team-chat channels

Revision ID: tc2a2b3c4d5e
Revises: ps1a2b3c4d5e
Create Date: 2026-10-22 00:00:00.000000

Copies the PR #4 ``session_chat_messages`` rows into ``chat_messages`` of a
``kind=session`` channel, one channel per distinct
``(workspace_id, root_conversation_id)``. Ids, ``created_by -> author_id``,
``created_at`` and ``body`` are kept; ``author_kind`` is ``'user'``.

Both steps are idempotent: an existing channel for a root is reused, and a
message whose id already exists in ``chat_messages`` is skipped. The legacy
table is intentionally NOT dropped here (ADR-0002 keeps it until R2). The
earlier cleanup revision dropped the table before this migration existed, so
a database that applied it has no rows left to copy and this migration is a
no-op; the test suite recreates and seeds the table before running it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16
from omnigent.db.utils import now_epoch_us

revision: str = "tc2a2b3c4d5e"
down_revision: str | None = "ps1a2b3c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_TABLE = "session_chat_messages"
# chat_messages.author_id is NOT NULL; legacy single-user rows stored
# created_by=NULL, which the team chat convention attributes to "local".
_SINGLE_USER_AUTHOR = "local"


def _channels_table() -> sa.TableClause:
    """A lightweight ``chat_channels`` table for the data copy."""
    return sa.table(
        "chat_channels",
        sa.column("workspace_id", sa.BigInteger()),
        sa.column("id", Uuid16()),
        sa.column("kind", sa.String(16)),
        sa.column("name", sa.String(64)),
        sa.column("topic", sa.Text()),
        sa.column("project_name", sa.String(256)),
        sa.column("visibility", sa.String(16)),
        sa.column("dm_key", sa.String(64)),
        sa.column("session_root_id", Uuid16()),
        sa.column("created_by", sa.String(128)),
        sa.column("created_at", sa.BigInteger()),
        sa.column("archived_at", sa.BigInteger()),
    )


def _messages_table() -> sa.TableClause:
    """A lightweight ``chat_messages`` table for the data copy."""
    return sa.table(
        "chat_messages",
        sa.column("workspace_id", sa.BigInteger()),
        sa.column("id", Uuid16()),
        sa.column("channel_id", Uuid16()),
        sa.column("thread_root_id", Uuid16()),
        sa.column("author_id", sa.String(160)),
        sa.column("author_kind", sa.String(16)),
        sa.column("created_at", sa.BigInteger()),
        sa.column("body", sa.Text()),
        sa.column("client_id", sa.String(64)),
    )


def _legacy_table() -> sa.TableClause:
    """A lightweight ``session_chat_messages`` table for the data copy."""
    return sa.table(
        _LEGACY_TABLE,
        sa.column("workspace_id", sa.BigInteger()),
        sa.column("root_conversation_id", Uuid16()),
        sa.column("id", Uuid16()),
        sa.column("created_by", sa.String(128)),
        sa.column("created_at", sa.BigInteger()),
        sa.column("body", sa.Text()),
    )


def upgrade() -> None:
    """Create a session channel per legacy root and copy its rows over."""
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_LEGACY_TABLE):
        # The earlier per-session human-chat cleanup already dropped the
        # legacy table, so there is nothing left to migrate.
        return

    channels = _channels_table()
    messages = _messages_table()
    legacy = _legacy_table()

    roots = bind.execute(
        sa.select(legacy.c.workspace_id, legacy.c.root_conversation_id).distinct()
    ).all()

    channel_ids: dict[tuple[int, str], str] = {}
    for workspace_id, root_id in roots:
        existing = bind.execute(
            sa.select(channels.c.id).where(
                channels.c.workspace_id == workspace_id,
                channels.c.kind == "session",
                channels.c.session_root_id == root_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            channel_ids[(workspace_id, root_id)] = existing
            continue
        channel_id = uuid.uuid4().hex
        bind.execute(
            channels.insert().values(
                workspace_id=workspace_id,
                id=channel_id,
                kind="session",
                visibility="public",
                session_root_id=root_id,
                created_at=now_epoch_us(),
            )
        )
        channel_ids[(workspace_id, root_id)] = channel_id

    for row in bind.execute(sa.select(legacy)).all():
        exists = bind.execute(
            sa.select(messages.c.id).where(
                messages.c.workspace_id == row.workspace_id,
                messages.c.id == row.id,
            )
        ).scalar_one_or_none()
        if exists is not None:
            continue
        bind.execute(
            messages.insert().values(
                workspace_id=row.workspace_id,
                id=row.id,
                channel_id=channel_ids[(row.workspace_id, row.root_conversation_id)],
                author_id=row.created_by or _SINGLE_USER_AUTHOR,
                author_kind="user",
                created_at=row.created_at,
                body=row.body,
            )
        )


def downgrade() -> None:
    """Remove the session channels and the messages they carry."""
    bind = op.get_bind()
    channels = _channels_table()
    messages = _messages_table()

    session_channel_ids = sa.select(channels.c.id).where(channels.c.kind == "session")
    bind.execute(messages.delete().where(messages.c.channel_id.in_(session_channel_ids)))
    bind.execute(channels.delete().where(channels.c.kind == "session"))
