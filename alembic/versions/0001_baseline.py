"""baseline schema — all Option B tables

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-23

Creates the complete initial Friendex schema: every table in the
§Persistence Strategy "Option B" design, extended with the ``guild_id``
dimension from ADR-0001 (per-guild markets).

**No-drift by construction.** Rather than hand-transcribing ``op.create_table``
calls (which inevitably drift from ``orm.py``), this baseline drives DDL
straight off ``Base.metadata`` — the single registry the ORM classes populate.
``upgrade()`` issues ``metadata.create_all`` and ``downgrade()`` issues
``metadata.drop_all`` against the migration's bound connection, so the migrated
schema is, by construction, byte-for-byte the schema the ORM defines. SQLAlchemy
orders ``drop_all`` to respect the foreign-key dependency graph, so the
downgrade is FK-safe without manual sequencing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

# ``friendex.adapters.persistence.orm`` is imported for its side effect: the ORM
# classes register themselves on ``Base.metadata`` at definition time, so without
# this import ``create_all`` would see an empty registry. ``noqa: F401`` marks the
# otherwise-"unused" import as intentional.
import friendex.adapters.persistence.orm  # noqa: F401
from friendex.adapters.persistence.db import Base

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create every table defined on ``Base.metadata``."""
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    """Drop every table defined on ``Base.metadata`` (FK-safe order)."""
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
