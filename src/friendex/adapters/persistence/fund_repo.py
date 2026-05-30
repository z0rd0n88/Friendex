"""SQLAlchemy-backed :class:`IFundRepo` adapter for the hedge-fund aggregate.

``SqlFundRepository`` persists and rebuilds a whole :class:`HedgeFund` — its
scalar row (``hedge_funds``) plus its investor stakes (``fund_investors``). It
conforms to :class:`~friendex.application.interfaces.IFundRepo` *structurally*
(Protocol duck-typing); it deliberately does **not** inherit from it, keeping
the dependency arrow pointing inward (``adapters -> application -> domain``).

**Aggregate persistence.** :meth:`upsert` is an idempotent delete-then-insert of
the whole aggregate inside one transaction: the scalar row is ``merge``d, then
its investor rows are deleted and re-inserted from the ``investors`` dict. This
keeps the mapping a pure function of the aggregate (no diff logic) and
guarantees a removed investor never lingers. (Because the parent is ``merge``d,
not deleted, investors do not cascade on upsert — the explicit wipe is required.)

**Events wallet.** :meth:`ensure_events_wallet` is the idempotent
get-or-create for the per-guild ``events_wallet`` pseudo-fund: it returns the
existing wallet untouched when present (no balance mutation) and creates an
empty one at $0 otherwise.

**Eager loading.** :meth:`list_all` loads every fund's investors in a single
extra query grouped in memory, so listing N funds never fans out into N investor
queries (no N+1).

**Deletion.** :meth:`delete` issues a single ``DELETE`` of the parent fund row
and relies on the DB-level ``ON DELETE CASCADE`` (ADR-0002) to remove its
investors — no hand-rolled child cleanup.

**Invariants preserved.** Money stays :class:`~decimal.Decimal` (exact value and
quantisation, via ``DecimalText``) across the boundary; the mapper builds fresh
domain objects and never mutates the loaded rows.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from friendex.adapters.persistence.orm import FundInvestorORM, HedgeFundORM
from friendex.adapters.persistence.unit_of_work import current_session
from friendex.domain.models import HedgeFund

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# The per-guild treasury pseudo-fund (see ADR / §fund send_events). Created
# empty and zero-balance on first ``ensure_events_wallet`` call.
_EVENTS_WALLET_ID = "events_wallet"
_EVENTS_WALLET_NAME = "Events Wallet"
# A pseudo-fund has no human manager; "0" is the sentinel manager id.
_EVENTS_WALLET_MANAGER_ID = "0"
_ZERO_CASH = Decimal("0.00")


class SqlFundRepository:
    """Persist :class:`HedgeFund` aggregates (scalars + investors) via async SQLAlchemy.

    Constructed with an :class:`async_sessionmaker`; each public method opens a
    short-lived session so callers never share session state across operations
    (one transaction per call, matching the repository contract).
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def get(self, guild_id: str, fund_id: str) -> HedgeFund | None:
        """Return the fund for ``(guild_id, fund_id)`` or ``None``.

        When a :class:`~friendex.adapters.persistence.unit_of_work.SqlUnitOfWork`
        transaction is active the read joins the shared session so it
        observes the in-flight writes and does not provoke a StaticPool
        rollback of the outer transaction.

        Persistence failures (engine error, connection drop, etc.) MUST
        propagate — this is the contract the trading service's
        :meth:`_get_fund_cash` ghost-fund guard (#84 H) relies on. The
        method's only documented return for absence is ``None``; any
        exception escapes to the caller.
        """
        shared = current_session()
        if shared is not None:
            row = await self._load_fund_row(shared, guild_id, fund_id)
            if row is None:
                return None
            investors = await self._load_investors(shared, guild_id, fund_id)
            return row.to_domain(investors)
        async with self._sessionmaker() as session:
            row = await self._load_fund_row(session, guild_id, fund_id)
            if row is None:
                return None
            investors = await self._load_investors(session, guild_id, fund_id)
            return row.to_domain(investors)

    async def upsert(self, guild_id: str, fund: HedgeFund) -> None:
        """Insert or replace ``fund`` (and its investors) under ``guild_id``.

        When a :class:`~friendex.adapters.persistence.unit_of_work.SqlUnitOfWork`
        transaction is active the upsert enrols into the shared session (the
        UoW owns the commit so a sibling write failure can roll the row back);
        otherwise it falls back to the per-call session that every legacy
        caller relies on.

        M-1 (PR #92 review carry-forward of #82 H10) — ``session.flush()`` is
        called explicitly after ``merge`` so the parent row is materialised
        before the child DELETE / INSERT chain runs. Today's
        ``autoflush=True`` default would do this implicitly, but a future
        flip to ``autoflush=False`` (a hardening sweep, or any session-config
        drift) would silently break the merge → delete → insert ordering and
        trip the FK constraint on the investor re-inserts (``FundInvestorORM``
        has a composite FK back to ``hedge_funds``). The explicit flush keeps
        the contract independent of the default — applied identically on both
        the shared-session and per-call paths, byte-equivalent to the
        ``SqlUserRepository.upsert`` pattern.
        """
        shared = current_session()
        if shared is not None:
            await shared.merge(HedgeFundORM.from_domain(guild_id, fund))
            await shared.flush()
            await self._delete_investors(shared, guild_id, fund.fund_id)
            self._insert_investors(shared, guild_id, fund)
            await shared.flush()
            return
        async with self._sessionmaker() as session:
            await session.merge(HedgeFundORM.from_domain(guild_id, fund))
            await session.flush()
            await self._delete_investors(session, guild_id, fund.fund_id)
            self._insert_investors(session, guild_id, fund)
            await session.commit()

    async def delete(self, guild_id: str, fund_id: str) -> None:
        """Delete the fund; investor rows cascade at the DB level (ADR-0002).

        Same shared-session opt-in as :meth:`upsert` — joins the active UoW
        session when one is set, otherwise opens its own short-lived session.
        """
        shared = current_session()
        if shared is not None:
            await shared.execute(
                delete(HedgeFundORM).where(
                    HedgeFundORM.guild_id == guild_id,
                    HedgeFundORM.fund_id == fund_id,
                )
            )
            await shared.flush()
            return
        async with self._sessionmaker() as session:
            await session.execute(
                delete(HedgeFundORM).where(
                    HedgeFundORM.guild_id == guild_id,
                    HedgeFundORM.fund_id == fund_id,
                )
            )
            await session.commit()

    async def list_all(self, guild_id: str) -> list[HedgeFund]:
        """Return every fund in ``guild_id``, each with its investors rebuilt.

        Investors for all funds are loaded in a single query and grouped in
        memory to avoid an N+1 fan-out across the listed funds. Same
        shared-session opt-in as :meth:`get`.
        """
        shared = current_session()
        if shared is not None:
            fund_rows = (
                (
                    await shared.execute(
                        select(HedgeFundORM).where(HedgeFundORM.guild_id == guild_id)
                    )
                )
                .scalars()
                .all()
            )
            investors_by_fund = await self._load_investors_by_fund(shared, guild_id)
            return [row.to_domain(investors_by_fund[row.fund_id]) for row in fund_rows]
        async with self._sessionmaker() as session:
            fund_rows = (
                (
                    await session.execute(
                        select(HedgeFundORM).where(HedgeFundORM.guild_id == guild_id)
                    )
                )
                .scalars()
                .all()
            )
            investors_by_fund = await self._load_investors_by_fund(session, guild_id)
            return [row.to_domain(investors_by_fund[row.fund_id]) for row in fund_rows]

    async def ensure_events_wallet(self, guild_id: str) -> HedgeFund:
        """Return the guild's ``events_wallet`` pseudo-fund, creating it if absent.

        Idempotent: a repeat call returns the existing wallet without mutating
        its balance.
        """
        existing = await self.get(guild_id, _EVENTS_WALLET_ID)
        if existing is not None:
            return existing

        wallet = HedgeFund(
            fund_id=_EVENTS_WALLET_ID,
            name=_EVENTS_WALLET_NAME,
            manager_id=_EVENTS_WALLET_MANAGER_ID,
            cash_balance=_ZERO_CASH,
            investors={},
        )
        await self.upsert(guild_id, wallet)
        return wallet

    # -- internal helpers ---------------------------------------------------

    @staticmethod
    async def _load_fund_row(
        session: AsyncSession, guild_id: str, fund_id: str
    ) -> HedgeFundORM | None:
        return (
            await session.execute(
                select(HedgeFundORM).where(
                    HedgeFundORM.guild_id == guild_id,
                    HedgeFundORM.fund_id == fund_id,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def _load_investors(
        session: AsyncSession, guild_id: str, fund_id: str
    ) -> dict[str, Decimal]:
        """Load one fund's investor stakes as an ``investor_id -> amount`` dict."""
        rows = (
            (
                await session.execute(
                    select(FundInvestorORM).where(
                        FundInvestorORM.guild_id == guild_id,
                        FundInvestorORM.fund_id == fund_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        return {row.investor_id: row.to_amount() for row in rows}

    @staticmethod
    async def _load_investors_by_fund(
        session: AsyncSession, guild_id: str
    ) -> dict[str, dict[str, Decimal]]:
        """Load all of a guild's investors in one query, grouped by ``fund_id``.

        The single query plus in-memory grouping replaces a per-fund investor
        fetch, eliminating the N+1 in :meth:`list_all`.
        """
        rows = (
            (
                await session.execute(
                    select(FundInvestorORM).where(FundInvestorORM.guild_id == guild_id)
                )
            )
            .scalars()
            .all()
        )
        grouped: dict[str, dict[str, Decimal]] = defaultdict(dict)
        for row in rows:
            grouped[row.fund_id][row.investor_id] = row.to_amount()
        return grouped

    @staticmethod
    def _insert_investors(
        session: AsyncSession, guild_id: str, fund: HedgeFund
    ) -> None:
        """Stage every investor stake of ``fund`` for insertion."""
        session.add_all(
            FundInvestorORM.from_domain(guild_id, fund.fund_id, investor_id, amount)
            for investor_id, amount in fund.investors.items()
        )

    @staticmethod
    async def _delete_investors(
        session: AsyncSession, guild_id: str, fund_id: str
    ) -> None:
        """Delete all investor rows for a fund ahead of a re-insert."""
        await session.execute(
            delete(FundInvestorORM).where(
                FundInvestorORM.guild_id == guild_id,
                FundInvestorORM.fund_id == fund_id,
            )
        )
