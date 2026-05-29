"""SQLAlchemy-backed :class:`ISystemStateRepo` adapter for per-guild reset state.

``SqlSystemStateRepository`` persists and rebuilds a :class:`SystemState` — a
single scalar row per guild in ``system_state`` keyed by ``guild_id`` alone, with
no child tables and no foreign keys. It conforms to
:class:`~friendex.application.interfaces.ISystemStateRepo` *structurally*
(Protocol duck-typing); it deliberately does **not** inherit from it, keeping the
dependency arrow pointing inward (``adapters -> application -> domain``).

**One row per guild.** ``guild_id`` is the whole primary key, so :meth:`upsert`
(``session.merge`` on a fixed PK) is an UPDATE on the repeat call — two upserts
collapse to exactly one row, never a duplicate.

**Sensible default.** :meth:`get` returns ``None`` for a guild with no state row;
the daily/weekly reset tasks read that as "never reset yet" and seed a row on
their first run.

**Unscoped ``list_all``.** The reset tasks iterate *every* guild, so
:meth:`list_all` takes no ``guild_id`` and returns one DTO per guild.

**Invariants preserved.** ``last_daily_reset`` / ``last_weekly_reset`` stay
tz-aware UTC (via ``UtcDateTime``) or ``None`` across the boundary; the mapper
builds fresh DTOs and never mutates loaded rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from friendex.adapters.persistence.orm import SystemStateORM
from friendex.application.interfaces import SystemState

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _to_dto(row: SystemStateORM) -> SystemState:
    """Build a fresh :class:`SystemState` DTO from an ORM row (pure, immutable)."""
    return SystemState(
        guild_id=row.guild_id,
        last_daily_reset=row.last_daily_reset,
        last_weekly_reset=row.last_weekly_reset,
        last_monthly_rollover=row.last_monthly_rollover,
    )


class SqlSystemStateRepository:
    """Persist per-guild background-task reset state via async SQLAlchemy.

    Constructed with an :class:`async_sessionmaker`; each public method opens a
    short-lived session so callers never share session state across operations
    (one transaction per call, matching the repository contract).
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def get(self, guild_id: str) -> SystemState | None:
        """Return the state row for ``guild_id`` or ``None`` (never reset)."""
        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    select(SystemStateORM).where(SystemStateORM.guild_id == guild_id)
                )
            ).scalar_one_or_none()
            return None if row is None else _to_dto(row)

    async def upsert(self, state: SystemState) -> None:
        """Insert or replace the state row (scope carried in the DTO).

        Idempotent on the fixed ``guild_id`` PK: a repeat call is an UPDATE, so
        no duplicate row is ever created.
        """
        async with self._sessionmaker() as session:
            await session.merge(
                SystemStateORM.create(
                    state.guild_id,
                    last_daily_reset=state.last_daily_reset,
                    last_weekly_reset=state.last_weekly_reset,
                    last_monthly_rollover=state.last_monthly_rollover,
                )
            )
            await session.commit()

    async def delete(self, guild_id: str) -> None:
        """Delete the state row for ``guild_id`` (no children to cascade)."""
        async with self._sessionmaker() as session:
            await session.execute(
                delete(SystemStateORM).where(SystemStateORM.guild_id == guild_id)
            )
            await session.commit()

    async def list_all(self) -> list[SystemState]:
        """Return the state row for every guild (unscoped)."""
        async with self._sessionmaker() as session:
            rows = (await session.execute(select(SystemStateORM))).scalars().all()
            return [_to_dto(row) for row in rows]
