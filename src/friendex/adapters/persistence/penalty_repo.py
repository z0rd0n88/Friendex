"""SQLAlchemy-backed :class:`IPenaltyRepo` adapter for early-withdrawal penalties.

``SqlPenaltyRepository`` persists and rebuilds a :class:`FundPenalty` — a flat
scalar row in ``fund_penalties`` keyed by ``(guild_id, user_id)``, with no child
tables and no foreign keys. It conforms to
:class:`~friendex.application.interfaces.IPenaltyRepo` *structurally* (Protocol
duck-typing); it deliberately does **not** inherit from it, keeping the
dependency arrow pointing inward (``adapters -> application -> domain``).

**Plain store, not a filter.** Unlike the cooldown repo, this repo does no TTL
filtering: :meth:`get` returns a penalty even when its ``penalty_until`` is in
the past, and :meth:`list_all` surfaces both live and expired penalties. The
penalty-decay task relies on seeing expired rows so it can :meth:`delete` them,
and whether an expired penalty *applies* is a domain decision
(``fund_math.compute_effective_apy``), not a persistence one.

**Deletion.** :meth:`delete` is a single ``DELETE`` of the row; there are no
child rows to cascade.

**Invariants preserved.** ``penalty_apr`` stays :class:`~decimal.Decimal` (exact
value and quantisation, via ``DecimalText``) and ``penalty_until`` stays tz-aware
UTC (via ``UtcDateTime``) across the boundary; the mapper builds fresh domain
objects and never mutates the loaded rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from friendex.adapters.persistence.orm import FundPenaltyORM

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from friendex.domain.models import FundPenalty


class SqlPenaltyRepository:
    """Persist :class:`FundPenalty` rows via async SQLAlchemy.

    Constructed with an :class:`async_sessionmaker`; each public method opens a
    short-lived session so callers never share session state across operations
    (one transaction per call, matching the repository contract).
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def get(self, guild_id: str, user_id: str) -> FundPenalty | None:
        """Return the penalty for ``(guild_id, user_id)`` or ``None``.

        An expired penalty (``penalty_until`` in the past) is still returned —
        the repo does not interpret expiry.
        """
        async with self._sessionmaker() as session:
            row = await self._load_row(session, guild_id, user_id)
            return None if row is None else row.to_domain()

    async def upsert(self, guild_id: str, penalty: FundPenalty) -> None:
        """Insert or replace ``penalty`` under ``guild_id``."""
        async with self._sessionmaker() as session:
            await session.merge(FundPenaltyORM.from_domain(guild_id, penalty))
            await session.commit()

    async def delete(self, guild_id: str, user_id: str) -> None:
        """Delete the penalty for ``(guild_id, user_id)`` (no children to cascade)."""
        async with self._sessionmaker() as session:
            await session.execute(
                delete(FundPenaltyORM).where(
                    FundPenaltyORM.guild_id == guild_id,
                    FundPenaltyORM.user_id == user_id,
                )
            )
            await session.commit()

    async def list_all(self, guild_id: str) -> list[FundPenalty]:
        """Return every penalty in ``guild_id`` (live and expired alike)."""
        async with self._sessionmaker() as session:
            rows = (
                (
                    await session.execute(
                        select(FundPenaltyORM).where(
                            FundPenaltyORM.guild_id == guild_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            return [row.to_domain() for row in rows]

    # -- internal helpers ---------------------------------------------------

    @staticmethod
    async def _load_row(
        session: AsyncSession, guild_id: str, user_id: str
    ) -> FundPenaltyORM | None:
        return (
            await session.execute(
                select(FundPenaltyORM).where(
                    FundPenaltyORM.guild_id == guild_id,
                    FundPenaltyORM.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
