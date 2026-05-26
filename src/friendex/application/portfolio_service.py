"""Read-only portfolio and net-worth use cases for the Phase 8d services.

:class:`PortfolioService` mediates between the ``/portfolio`` and ``/balance``
slash commands (Phase 11 cogs) and the persistence ports, plus the monthly
``MonthlyRolloverTask`` (Phase 9) that snapshots every account's
``month_start_net_worth``.

**Read paths are lockless** (per the Phase 8d spec). ``calculate_net_worth``
and ``portfolio_snapshot`` are best-effort reads — a concurrent trade or
activity tick landing mid-read is tolerated, and the worst case is a
snapshot that mixes the pre- and post-trade view of one position. The Phase
10 embed builders consume the resulting :class:`PortfolioSnapshot` directly,
so they never see a partially-updated aggregate.

**The one write path — ``capture_month_start_net_worth`` — takes a per-user
``LockManager.locked`` lock per account.** The monthly rollover is
independent per user (no cross-user races), but a concurrent trade landing
between the per-user read and the per-user ``upsert`` would clobber the
trade's cash/position update. We therefore lock per user, read, recompute net
worth, and round-trip via :func:`dataclasses.replace` inside the critical
section — mirroring the per-account sweep in
:meth:`TradingService.update_frozen_shorts` (Phase 8c digest §convention 1).

**Guild scoping (ADR-0001 / Phase 8a digest).** ``guild_id`` is a constructor
argument captured once as ``self._guild_id``; domain models stay
guild-agnostic. Lock keys use the composite ``"<guild_id>:<user_id>"`` shape
built by :meth:`_lock_key` so the shared :class:`LockManager` Phase 14
injects across every per-guild scope cannot serialise unrelated guilds
against each other.

**Net-worth computation is delegated to**
:func:`friendex.domain.fund_math.compute_net_worth` — the service is a pure
orchestrator, not a re-implementation of the math (Phase 4 digest contract).
The user's personal hedge fund is looked up by ``fund_id == user_id``,
matching the Phase 8a/8c convention.

**Immutability.** Every persisted aggregate is replaced via
:func:`dataclasses.replace`; no in-place mutation of stored references
(matches the fake-repo / SQLite parity invariant from the Phase 8 fakes
digest).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

from friendex.application.snapshot_models import PortfolioSnapshot
from friendex.domain.fund_math import compute_net_worth

if TYPE_CHECKING:
    from friendex.adapters.config import Settings
    from friendex.application.interfaces import (
        IFundRepo,
        IPriceRepo,
        IUserRepo,
    )
    from friendex.application.lock_manager import LockManager
    from friendex.domain.models import Stock, UserAccount

# Personal fund cash balance for a user who has never created one.
_ZERO_CASH = Decimal("0.00")


class PortfolioService:
    """Read-only portfolio + monthly net-worth rollover use cases."""

    def __init__(
        self,
        *,
        guild_id: str,
        user_repo: IUserRepo,
        price_repo: IPriceRepo,
        fund_repo: IFundRepo,
        lock_manager: LockManager,
        settings: Settings,
    ) -> None:
        self._guild_id = guild_id
        self._user_repo = user_repo
        self._price_repo = price_repo
        self._fund_repo = fund_repo
        self._locks = lock_manager
        self._settings = settings

    # -- internal helpers ---------------------------------------------------

    def _lock_key(self, user_id: str) -> str:
        """Return the composite ``"<guild>:<user>"`` lock key (ADR-0001)."""
        return f"{self._guild_id}:{user_id}"

    async def _personal_fund_cash(self, user_id: str) -> Decimal:
        """Return the cash balance of ``user_id``'s personal hedge fund, or 0."""
        fund = await self._fund_repo.get(self._guild_id, user_id)
        return fund.cash_balance if fund is not None else _ZERO_CASH

    async def _prices_for_account(self, account: UserAccount) -> dict[str, Stock]:
        """Return the ``{target_user_id: Stock}`` map needed to value ``account``.

        Only the targets the account actually references (long + short) are
        loaded — the full guild scan is unnecessary for a single net-worth
        read. A target with no stored stock is omitted; ``compute_net_worth``
        already handles that case by contributing zero for the price-valued
        portion.
        """
        target_ids = set(account.long_positions) | set(account.short_positions)
        prices: dict[str, Stock] = {}
        for target_id in target_ids:
            stock = await self._price_repo.get(self._guild_id, target_id)
            if stock is not None:
                prices[target_id] = stock
        return prices

    async def _compute_net_worth_for(self, account: UserAccount) -> Decimal:
        """Compose price + fund lookups and delegate to the domain helper."""
        prices = await self._prices_for_account(account)
        fund = await self._fund_repo.get(self._guild_id, account.user_id)
        return compute_net_worth(account, prices, fund)

    # -- public read use cases (lockless) ----------------------------------

    async def calculate_net_worth(self, user_id: str) -> Decimal | None:
        """Return ``user_id``'s rolled-up net worth, or ``None`` if absent.

        Composes the price + fund lookups the domain helper needs and
        delegates the math to :func:`fund_math.compute_net_worth`. Best-effort
        read — no lock is held, so a concurrent trade may land mid-read; the
        returned :class:`Decimal` is therefore a snapshot, not a guarantee.
        """
        account = await self._user_repo.get(self._guild_id, user_id)
        if account is None:
            return None
        return await self._compute_net_worth_for(account)

    async def portfolio_snapshot(self, user_id: str) -> PortfolioSnapshot | None:
        """Return the read-model snapshot for ``/portfolio`` / ``/balance``.

        Returns ``None`` for an unknown user. The result is a frozen
        :class:`PortfolioSnapshot` — the embed builder consumes it as-is and
        does not re-read state.
        """
        account = await self._user_repo.get(self._guild_id, user_id)
        if account is None:
            return None
        net_worth = await self._compute_net_worth_for(account)
        fund_cash = await self._personal_fund_cash(user_id)
        return PortfolioSnapshot(
            user_id=account.user_id,
            cash_balance=account.cash_balance,
            net_worth=net_worth,
            month_start_net_worth=account.month_start_net_worth,
            fund_balance=fund_cash,
            long_positions=dict(account.long_positions),
            short_positions=dict(account.short_positions),
        )

    # -- monthly write path (per-user lock) --------------------------------

    async def capture_month_start_net_worth(self) -> None:
        """Snapshot every account's current net worth as the month's baseline.

        Walks every account in the guild and, per user, takes
        ``self._locks.locked(self._lock_key(user_id))``, recomputes net worth
        inside the lock, and round-trips a replaced :class:`UserAccount` with
        both ``net_worth`` and ``month_start_net_worth`` set to the freshly
        computed value. The per-user lock prevents a concurrent trade or tick
        landing between the read and the ``upsert`` from being clobbered.

        Called by the Phase 9 ``MonthlyRolloverTask``; safe to retry — the
        write is idempotent for a given (account, price) state.
        """
        accounts = await self._user_repo.list_all(self._guild_id)
        for account in accounts:
            async with self._locks.locked(self._lock_key(account.user_id)):
                fresh = await self._user_repo.get(self._guild_id, account.user_id)
                if fresh is None:
                    continue
                net_worth = await self._compute_net_worth_for(fresh)
                snapshot = replace(
                    fresh,
                    net_worth=net_worth,
                    month_start_net_worth=net_worth,
                )
                await self._user_repo.upsert(self._guild_id, snapshot)
