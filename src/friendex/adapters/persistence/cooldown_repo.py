"""SQLAlchemy-backed :class:`ITradeCooldownRepo` adapter with TTL semantics.

``SqlTradeCooldownRepository`` persists short/cover cooldowns — a flat scalar
row in ``trade_cooldowns`` keyed by ``(guild_id, user_id)``, with no child tables
and no foreign keys. It conforms to
:class:`~friendex.application.interfaces.ITradeCooldownRepo` *structurally*
(Protocol duck-typing); it deliberately does **not** inherit from it, keeping the
dependency arrow pointing inward (``adapters -> application -> domain``).

**TTL via ``expires_at`` (replaces Redis-native TTL).** :meth:`get` returns a
cooldown only while it is *active* — a row whose ``expires_at <= now`` is treated
as expired and excluded (inclusive ``<=``: TTL has elapsed *at* ``now``). The
``now`` cutoff is a required keyword-only parameter so the active-vs-expired
filter is part of the contract — callers (the trading service, background sweeps,
tests under ``freeze_time``) pass a deterministic UTC instant.

**Bulk purge.** :meth:`purge_expired` is a single
``DELETE ... WHERE expires_at <= now`` across every guild (unscoped sweep) — no
load-then-delete loop — and returns the affected row count. The ``<=`` boundary
matches :meth:`get` so a row is never simultaneously "hidden by get" yet
"survives the purge".

**``list_all`` is unfiltered:** it returns every row in the guild, expired
included, for callers (e.g. diagnostics) that need the raw set.

**Scope-in-DTO.** The payload is the application-layer :class:`TradeCooldown`
DTO, which carries ``guild_id`` inside it, so :meth:`upsert` takes no separate
``guild_id`` argument.

**Invariants preserved.** ``expires_at`` stays tz-aware UTC (via ``UtcDateTime``)
across the boundary; the mapper builds fresh DTOs and never mutates loaded rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sqlalchemy import delete, select

from friendex.adapters.persistence.orm import TradeCooldownORM
from friendex.adapters.persistence.unit_of_work import current_session
from friendex.application.interfaces import TradeCooldown

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.engine import CursorResult
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _to_dto(row: TradeCooldownORM) -> TradeCooldown:
    """Build a fresh :class:`TradeCooldown` DTO from an ORM row (pure, immutable)."""
    return TradeCooldown(
        guild_id=row.guild_id,
        user_id=row.user_id,
        expires_at=row.expires_at,
    )


class SqlTradeCooldownRepository:
    """Persist short/cover cooldowns with TTL semantics via async SQLAlchemy.

    Constructed with an :class:`async_sessionmaker`; each public method opens a
    short-lived session so callers never share session state across operations
    (one transaction per call, matching the repository contract).
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def get(
        self, guild_id: str, user_id: str, *, now: datetime
    ) -> TradeCooldown | None:
        """Return the *active* cooldown, or ``None`` if absent or expired.

        A row is expired (and excluded) once ``expires_at <= now``. ``now`` is
        keyword-only and required, so callers must pass a deterministic UTC
        instant (matches :class:`~friendex.application.interfaces.ITradeCooldownRepo`).

        When a :class:`~friendex.adapters.persistence.unit_of_work.SqlUnitOfWork`
        transaction is active the read joins the shared session so the
        in-lock re-check (#82 C1) reads from the same session that holds
        the not-yet-committed first-leg writes.
        """
        shared = current_session()
        if shared is not None:
            row = (
                await shared.execute(
                    select(TradeCooldownORM).where(
                        TradeCooldownORM.guild_id == guild_id,
                        TradeCooldownORM.user_id == user_id,
                        TradeCooldownORM.expires_at > now,
                    )
                )
            ).scalar_one_or_none()
            return None if row is None else _to_dto(row)
        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    select(TradeCooldownORM).where(
                        TradeCooldownORM.guild_id == guild_id,
                        TradeCooldownORM.user_id == user_id,
                        TradeCooldownORM.expires_at > now,
                    )
                )
            ).scalar_one_or_none()
            return None if row is None else _to_dto(row)

    async def upsert(self, cooldown: TradeCooldown) -> None:
        """Insert or replace a cooldown (scope carried in the DTO).

        When a :class:`~friendex.adapters.persistence.unit_of_work.SqlUnitOfWork`
        transaction is active the cooldown row enrols into the shared
        session so a mid-``short``/``cover`` failure rolls it back along
        with the money writes.
        """
        shared = current_session()
        if shared is not None:
            await shared.merge(
                TradeCooldownORM.create(
                    cooldown.guild_id,
                    user_id=cooldown.user_id,
                    expires_at=cooldown.expires_at,
                )
            )
            await shared.flush()
            return
        async with self._sessionmaker() as session:
            await session.merge(
                TradeCooldownORM.create(
                    cooldown.guild_id,
                    user_id=cooldown.user_id,
                    expires_at=cooldown.expires_at,
                )
            )
            await session.commit()

    async def delete(self, guild_id: str, user_id: str) -> None:
        """Delete the cooldown for ``(guild_id, user_id)``.

        Same shared-session opt-in as :meth:`upsert`.
        """
        shared = current_session()
        if shared is not None:
            await shared.execute(
                delete(TradeCooldownORM).where(
                    TradeCooldownORM.guild_id == guild_id,
                    TradeCooldownORM.user_id == user_id,
                )
            )
            await shared.flush()
            return
        async with self._sessionmaker() as session:
            await session.execute(
                delete(TradeCooldownORM).where(
                    TradeCooldownORM.guild_id == guild_id,
                    TradeCooldownORM.user_id == user_id,
                )
            )
            await session.commit()

    async def list_all(self, guild_id: str) -> list[TradeCooldown]:
        """Return every cooldown row in ``guild_id`` (including expired ones).

        Same shared-session opt-in as :meth:`get`.
        """
        shared = current_session()
        if shared is not None:
            rows = (
                (
                    await shared.execute(
                        select(TradeCooldownORM).where(
                            TradeCooldownORM.guild_id == guild_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            return [_to_dto(row) for row in rows]
        async with self._sessionmaker() as session:
            rows = (
                (
                    await session.execute(
                        select(TradeCooldownORM).where(
                            TradeCooldownORM.guild_id == guild_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            return [_to_dto(row) for row in rows]

    async def purge_expired(self, now: datetime) -> int:
        """Delete every cooldown whose ``expires_at <= now``; return the count.

        A single parameterised bulk
        ``DELETE FROM trade_cooldowns WHERE expires_at <= :now`` across every
        guild — never a load-then-delete loop. The ``<=`` boundary matches
        :meth:`get` (TTL elapsed *at* ``now`` is expired).
        """
        async with self._sessionmaker() as session:
            result = cast(
                "CursorResult[object]",
                await session.execute(
                    delete(TradeCooldownORM).where(TradeCooldownORM.expires_at <= now)
                ),
            )
            await session.commit()
            return result.rowcount
