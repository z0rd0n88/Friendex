"""SQLAlchemy-backed :class:`IPriceRepo` adapter for the stock aggregate.

``SqlPriceRepository`` persists and rebuilds a :class:`Stock` — its scalar row
(``stocks``) plus its append-only price history (``price_history``). It conforms
to :class:`~friendex.application.interfaces.IPriceRepo` *structurally* (Protocol
duck-typing); it deliberately does **not** inherit from it, keeping the
dependency arrow pointing inward (``adapters -> application -> domain``).

**History is append-only.** Unlike the user aggregate, ``upsert`` only touches
the scalar stock row (``merge``); it never rewrites history. History grows via
:meth:`append_history` and is read back oldest-first by :meth:`get_history`. This
matches the contract: the scalar row and its history live in separate tables, so
history has dedicated methods rather than being round-tripped on every
``upsert``.

**Eager loading.** :meth:`list_all` loads every stock's history in a single
extra query via ``selectin``-style batching (one ``IN (...)`` select keyed by
``recorded_at`` ordering), so listing N stocks never fans out into N history
queries (no N+1).

**Bulk pruning.** :meth:`prune_history_older_than` is a single
``DELETE ... WHERE recorded_at < :cutoff`` across every guild — no load-then-
delete loop — and returns the affected row count.

**Deletion.** :meth:`delete` issues a single ``DELETE`` of the parent stock row
and relies on the DB-level ``ON DELETE CASCADE`` (ADR-0002) to remove its
history — no hand-rolled child cleanup.

**Invariants preserved.** Prices stay :class:`~decimal.Decimal` (exact value and
quantisation, via ``DecimalText``) and timestamps stay tz-aware UTC (via
``UtcDateTime``) across the boundary; the mapper builds fresh domain objects and
never mutates the loaded rows.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, cast

from sqlalchemy import delete, select

from friendex.adapters.persistence.orm import PriceHistoryORM, StockORM

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.engine import CursorResult
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from friendex.domain.models import PricePoint, Stock


class SqlPriceRepository:
    """Persist :class:`Stock` aggregates (scalars + price history) via async SQLAlchemy.

    Constructed with an :class:`async_sessionmaker`; each public method opens a
    short-lived session so callers never share session state across operations
    (one transaction per call, matching the repository contract).
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def get(self, guild_id: str, user_id: str) -> Stock | None:
        """Return the stock for ``(guild_id, user_id)`` or ``None``."""
        async with self._sessionmaker() as session:
            row = await self._load_stock_row(session, guild_id, user_id)
            if row is None:
                return None
            rows = await self._load_history(session, guild_id, user_id)
            return row.to_domain([r.to_domain() for r in rows])

    async def upsert(self, guild_id: str, stock: Stock) -> None:
        """Insert or replace the stock's scalar row under ``guild_id``.

        History is append-only and is **not** rewritten here — use
        :meth:`append_history`.
        """
        async with self._sessionmaker() as session:
            await session.merge(StockORM.from_domain(guild_id, stock))
            await session.commit()

    async def delete(self, guild_id: str, user_id: str) -> None:
        """Delete the stock; price history cascades at the DB level (ADR-0002)."""
        async with self._sessionmaker() as session:
            await session.execute(
                delete(StockORM).where(
                    StockORM.guild_id == guild_id, StockORM.user_id == user_id
                )
            )
            await session.commit()

    async def list_all(self, guild_id: str) -> list[Stock]:
        """Return every stock in ``guild_id``, each with its history rebuilt.

        History for all stocks is loaded in a single query and grouped in memory
        to avoid an N+1 fan-out across the listed stocks.
        """
        async with self._sessionmaker() as session:
            stock_rows = (
                (
                    await session.execute(
                        select(StockORM).where(StockORM.guild_id == guild_id)
                    )
                )
                .scalars()
                .all()
            )
            histories = await self._load_history_by_user(session, guild_id)
            return [
                row.to_domain([p.to_domain() for p in histories[row.user_id]])
                for row in stock_rows
            ]

    async def append_history(
        self, guild_id: str, user_id: str, point: PricePoint
    ) -> None:
        """Append one :class:`PricePoint` to a stock's history (append-only)."""
        async with self._sessionmaker() as session:
            session.add(PriceHistoryORM.from_domain(guild_id, user_id, point))
            await session.commit()

    async def get_history(
        self, guild_id: str, user_id: str, *, since: datetime | None = None
    ) -> list[PricePoint]:
        """Return a stock's price history, oldest first.

        ``since`` (tz-aware UTC) restricts the result to points at or after that
        instant.
        """
        async with self._sessionmaker() as session:
            rows = await self._load_history(session, guild_id, user_id, since=since)
            return [row.to_domain() for row in rows]

    async def prune_history_older_than(self, cutoff: datetime) -> int:
        """Delete all price-history rows older than ``cutoff``; return the count.

        A single parameterised bulk
        ``DELETE FROM price_history WHERE recorded_at < :cutoff`` across every
        guild — never a load-then-delete loop.
        """
        async with self._sessionmaker() as session:
            result = cast(
                "CursorResult[object]",
                await session.execute(
                    delete(PriceHistoryORM).where(PriceHistoryORM.recorded_at < cutoff)
                ),
            )
            await session.commit()
            return result.rowcount

    # -- internal helpers ---------------------------------------------------

    @staticmethod
    async def _load_stock_row(
        session: AsyncSession, guild_id: str, user_id: str
    ) -> StockORM | None:
        return (
            await session.execute(
                select(StockORM).where(
                    StockORM.guild_id == guild_id, StockORM.user_id == user_id
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def _load_history(
        session: AsyncSession,
        guild_id: str,
        user_id: str,
        *,
        since: datetime | None = None,
    ) -> list[PriceHistoryORM]:
        """Load one stock's history oldest-first, optionally restricted by ``since``."""
        stmt = select(PriceHistoryORM).where(
            PriceHistoryORM.guild_id == guild_id,
            PriceHistoryORM.user_id == user_id,
        )
        if since is not None:
            stmt = stmt.where(PriceHistoryORM.recorded_at >= since)
        stmt = stmt.order_by(PriceHistoryORM.recorded_at)
        return list((await session.execute(stmt)).scalars().all())

    @staticmethod
    async def _load_history_by_user(
        session: AsyncSession, guild_id: str
    ) -> dict[str, list[PriceHistoryORM]]:
        """Load all of a guild's history in one query, grouped by ``user_id``.

        The single ordered query plus in-memory grouping replaces a per-stock
        history fetch, eliminating the N+1 in :meth:`list_all`.
        """
        rows = (
            (
                await session.execute(
                    select(PriceHistoryORM)
                    .where(PriceHistoryORM.guild_id == guild_id)
                    .order_by(PriceHistoryORM.recorded_at)
                )
            )
            .scalars()
            .all()
        )
        grouped: dict[str, list[PriceHistoryORM]] = defaultdict(list)
        for row in rows:
            grouped[row.user_id].append(row)
        return grouped
