"""system_state: add last_portfolio_capture for split-step rollover bookkeeping

Revision ID: 0004_system_state_portfolio_capture
Revises: 0003_system_state_monthly_rollover
Create Date: 2026-05-29

Adds a nullable ``last_portfolio_capture DATE`` column to ``system_state`` so
the monthly rollover task can advance the portfolio-capture marker
independently of the (already present) ``last_monthly_rollover``
"both succeeded" marker.

Prior behaviour (PR #89): a fund-accrual failure left
``last_monthly_rollover`` unadvanced, which correctly triggered a replay on
the next tick — but the replay re-ran the portfolio capture step too, even
though it had already succeeded. The capture is idempotent at start-of-
month (the Phase 8e digest), so the replay is *safe*, but it duplicates
work and pollutes the audit trail.

The new column lets the task split the bookkeeping:

* ``last_portfolio_capture`` advances the moment
  :meth:`PortfolioService.capture_month_start_net_worth` returns.
* ``last_monthly_rollover`` still advances only after BOTH calls land.

On a fund-only replay the task now skips portfolio capture and re-runs
only :meth:`FundService.accrue_apy`. See PR #89 review L-1.

SQLite cannot ``ALTER`` a column type and gets fussy about ``DEFAULT`` on
``ADD COLUMN``, but ``ADD COLUMN`` with a nullable type is supported in
batch mode. The column is nullable so existing rows are valid without
backfill; the first tick that successfully runs portfolio capture seeds the
value.

**Idempotency note.** The baseline migration ``0001_baseline`` runs
``Base.metadata.create_all`` against the *current* ORM, which already
contains ``last_portfolio_capture``. A clean DB therefore already has the
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
revision: str = "0004_system_state_portfolio_capture"
down_revision: str | Sequence[str] | None = "0003_system_state_monthly_rollover"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "system_state"
_COLUMN = "last_portfolio_capture"


def _has_column(table: str, column: str) -> bool:
    """Return ``True`` iff ``column`` already exists on ``table``."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    """Add the nullable ``last_portfolio_capture`` column to ``system_state``.

    Idempotent: if baseline already created the column (clean deployments),
    skip the ``ADD COLUMN`` so this migration is a no-op rather than failing
    with ``duplicate column name``.
    """
    if _has_column(_TABLE, _COLUMN):
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column(_COLUMN, sa.Date(), nullable=True))


def downgrade() -> None:
    """Drop the ``last_portfolio_capture`` column from ``system_state``.

    Idempotent: if the column is already absent, do nothing.
    """
    if not _has_column(_TABLE, _COLUMN):
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column(_COLUMN)
