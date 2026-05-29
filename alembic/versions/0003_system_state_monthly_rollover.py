"""system_state: add last_monthly_rollover for durable rollover bookkeeping

Revision ID: 0003_system_state_monthly_rollover
Revises: 0002_fk_cascade
Create Date: 2026-05-29

Adds a nullable ``last_monthly_rollover DATE`` column to ``system_state`` so
the monthly rollover task can replay only the guilds whose stored rollover
date is older than the current UTC month (Wave 1 #82 C3).

Prior behaviour (Phase 9 AC8) gated firing on ``utcnow().day == 1 and
utcnow().hour == 0`` with a 1-hour cadence — a process restart, transient
service failure, or partial sweep at that exact hour silently skipped a
month's APY accrual. The new field gives the task a durable per-guild flag,
mirroring the pattern already in place for daily/weekly resets.

SQLite cannot ``ALTER`` a column type and gets fussy about ``DEFAULT`` on
``ADD COLUMN``, but ``ADD COLUMN`` with a nullable type is supported in
batch mode. We add the column nullable so existing rows are valid without
backfill; the first tick on a fresh deployment seeds the value, and the
upsert path always writes the field thereafter.

**Idempotency note.** The baseline migration ``0001_baseline`` runs
``Base.metadata.create_all`` against the *current* ORM, which already
contains ``last_monthly_rollover``. A clean DB therefore already has the
column after baseline runs; the inspector guard makes this upgrade a no-op
in that case, while still applying the ``ADD COLUMN`` on existing
deployments whose baseline ran before this column was added. The downgrade
likewise tolerates a column that has already been dropped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0003_system_state_monthly_rollover"
down_revision: str | Sequence[str] | None = "0002_fk_cascade"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "system_state"
_COLUMN = "last_monthly_rollover"


def _has_column(table: str, column: str) -> bool:
    """Return ``True`` iff ``column`` already exists on ``table``."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    """Add the nullable ``last_monthly_rollover`` column to ``system_state``.

    Idempotent: if baseline already created the column (clean deployments),
    skip the ``ADD COLUMN`` so this migration is a no-op rather than failing
    with ``duplicate column name``.
    """
    if _has_column(_TABLE, _COLUMN):
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column(_COLUMN, sa.Date(), nullable=True))


def downgrade() -> None:
    """Drop the ``last_monthly_rollover`` column from ``system_state``.

    Idempotent: if the column is already absent, do nothing.
    """
    if not _has_column(_TABLE, _COLUMN):
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column(_COLUMN)
