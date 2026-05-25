"""child FK columns get ON DELETE CASCADE

Revision ID: 0002_fk_cascade
Revises: 0001_baseline
Create Date: 2026-05-24

Activates the DB-level half of ADR-0002 (SQLite FK enforcement). The PRAGMA
listener in ``db.py`` turns enforcement *on*; this migration makes parent
deletes *cascade* to their children by giving every child foreign key an
``ON DELETE CASCADE`` action.

SQLite cannot ``ALTER`` a foreign-key action in place, so each child table is
rebuilt via Alembic batch mode (``op.batch_alter_table`` with ``copy_from`` +
``recreate="always"``): a new table is built from the supplied :class:`sa.Table`
definition — which carries the desired FK action — the data is copied across,
and the old table is dropped. ``env.py`` sets ``render_as_batch=True`` so the
move-and-copy DDL is emitted correctly.

The six child foreign keys (see ``orm.py``):

* ``long_positions(guild_id, owner_id)``   → ``users``
* ``short_positions(guild_id, owner_id)``  → ``users``
* ``activity_buckets(guild_id, user_id)``  → ``users``
* ``voice_unique_channels(...)``           → ``activity_buckets``
* ``price_history(guild_id, user_id)``     → ``stocks``
* ``fund_investors(guild_id, fund_id)``    → ``hedge_funds``

``upgrade`` rebuilds each with ``ondelete="CASCADE"``; ``downgrade`` rebuilds
each with a plain foreign key (no delete action), so the migration is fully
reversible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

from friendex.adapters.persistence.types import DecimalText, UtcDateTime

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0002_fk_cascade"
down_revision: str | Sequence[str] | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _child_tables(metadata: sa.MetaData, *, ondelete: str | None) -> list[sa.Table]:
    """Build the six child :class:`sa.Table` definitions for a recreate.

    Columns mirror ``orm.py`` exactly; only the foreign-key ``ondelete`` action
    varies by direction. ``DecimalText`` / ``UtcDateTime`` are reused so the
    recreated tables keep the same affinities as the ORM (no type drift).
    """
    return [
        sa.Table(
            "long_positions",
            metadata,
            sa.Column("guild_id", sa.String(), primary_key=True),
            sa.Column("owner_id", sa.String(), primary_key=True),
            sa.Column("target_id", sa.String(), primary_key=True),
            sa.Column("shares", sa.Integer(), nullable=False),
            sa.Column("avg_entry", DecimalText(), nullable=False),
            sa.ForeignKeyConstraint(
                ["guild_id", "owner_id"],
                ["users.guild_id", "users.user_id"],
                ondelete=ondelete,
            ),
        ),
        sa.Table(
            "short_positions",
            metadata,
            sa.Column("guild_id", sa.String(), primary_key=True),
            sa.Column("owner_id", sa.String(), primary_key=True),
            sa.Column("target_id", sa.String(), primary_key=True),
            sa.Column("shares", sa.Integer(), nullable=False),
            sa.Column("entry_price", DecimalText(), nullable=False),
            sa.Column("locked_cash", DecimalText(), nullable=False),
            sa.Column("locked_fund", DecimalText(), nullable=False),
            sa.Column("created_at", UtcDateTime(), nullable=False),
            sa.Column("frozen", sa.Boolean(), nullable=False),
            sa.ForeignKeyConstraint(
                ["guild_id", "owner_id"],
                ["users.guild_id", "users.user_id"],
                ondelete=ondelete,
            ),
        ),
        sa.Table(
            "activity_buckets",
            metadata,
            sa.Column("guild_id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), primary_key=True),
            sa.Column("bucket_type", sa.String(), primary_key=True),
            sa.Column("text_msgs", sa.Integer(), nullable=False),
            sa.Column("media_msgs", sa.Integer(), nullable=False),
            sa.Column("voice_minutes", sa.Float(), nullable=False),
            sa.Column("reaction_count", sa.Integer(), nullable=False),
            sa.Column("reply_count", sa.Integer(), nullable=False),
            sa.Column("role_ping_joins", sa.Float(), nullable=False),
            sa.Column("role_ping_join_minutes", sa.Float(), nullable=False),
            sa.Column("bucket_start", UtcDateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["guild_id", "user_id"],
                ["users.guild_id", "users.user_id"],
                ondelete=ondelete,
            ),
        ),
        sa.Table(
            "voice_unique_channels",
            metadata,
            sa.Column("guild_id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), primary_key=True),
            sa.Column("bucket_type", sa.String(), primary_key=True),
            sa.Column("channel_id", sa.String(), primary_key=True),
            sa.ForeignKeyConstraint(
                ["guild_id", "user_id", "bucket_type"],
                [
                    "activity_buckets.guild_id",
                    "activity_buckets.user_id",
                    "activity_buckets.bucket_type",
                ],
                ondelete=ondelete,
            ),
        ),
        sa.Table(
            "price_history",
            metadata,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("guild_id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("price", DecimalText(), nullable=False),
            sa.Column("recorded_at", UtcDateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["guild_id", "user_id"],
                ["stocks.guild_id", "stocks.user_id"],
                ondelete=ondelete,
            ),
            sa.Index("ix_price_history_lookup", "guild_id", "user_id", "recorded_at"),
        ),
        sa.Table(
            "fund_investors",
            metadata,
            sa.Column("guild_id", sa.String(), primary_key=True),
            sa.Column("fund_id", sa.String(), primary_key=True),
            sa.Column("investor_id", sa.String(), primary_key=True),
            sa.Column("invested_amount", DecimalText(), nullable=False),
            sa.ForeignKeyConstraint(
                ["guild_id", "fund_id"],
                ["hedge_funds.guild_id", "hedge_funds.fund_id"],
                ondelete=ondelete,
            ),
        ),
    ]


def _rewrite_child_fks(*, ondelete: str | None) -> None:
    """Recreate every child table with the given FK ``ondelete`` action.

    Uses an isolated :class:`sa.MetaData` per direction so the table objects do
    not collide with the ORM's registry or with each other across runs.
    """
    metadata = sa.MetaData()
    for table in _child_tables(metadata, ondelete=ondelete):
        with op.batch_alter_table(table.name, copy_from=table, recreate="always"):
            # ``copy_from`` supplies the full target definition (columns + the
            # desired FK); the recreate copies the data into it. No per-column
            # batch op is needed — the table is rebuilt wholesale.
            pass


def upgrade() -> None:
    """Give every child FK an ``ON DELETE CASCADE`` action."""
    _rewrite_child_fks(ondelete="CASCADE")


def downgrade() -> None:
    """Revert every child FK to a plain foreign key (no delete action)."""
    _rewrite_child_fks(ondelete=None)
