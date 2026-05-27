"""Application service that auto-covers shorts when they rally to the threshold.

:class:`LiquidationService` is the Phase 9
:class:`~friendex.adapters.tasks.liquidation_task.LiquidationTask`'s engine:
a 5-min sweep over every account in the guild looking for short positions
whose target price has rallied to at least
``entry_price * settings.liquidation_threshold`` (default 1.5x). Each
matching short is force-covered via
:meth:`TradingService._cover_internal(force=True)`, which bypasses the
:class:`~friendex.domain.errors.PositionFrozen` guard so a freshly-opened
short still inside its freeze window can still be liquidated.

**Why bypass the freeze guard.** The freeze window stops a user from
*manually* covering a brand-new short (preventing trivial round-tripping for
price-impact farming). A liquidation, by contrast, is a system action
triggered by the market moving against the holder — refusing to liquidate a
frozen short would just deepen the loss until the freeze expires.

**Locking discipline (Phase 7 + 8c).** The
:class:`~friendex.application.lock_manager.LockManager` is non-reentrant.
For each candidate short the sweep acquires **one** ``locked(holder,
target)`` block, re-reads the account inside the lock (the price may have
ticked again between the pre-lock scan and the lock acquisition), and only
then invokes :meth:`TradingService._cover_internal` — which by contract
does NOT re-take the lock (see its docstring). The sweep takes the lock
per-(holder, target) pair, never wrapping the whole iteration, so unrelated
accounts never serialise on a single liquidation run.

**Per-guild scope (ADR-0001).** The service is per-guild — ``guild_id`` is
a constructor argument; the iteration walks one guild's accounts and
markets at a time.

**No cooldown side-effect.** Liquidations bypass the short/cover cooldown.
The cooldown only governs *user-initiated* short/cover commands; a system
liquidation pre-empts it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from friendex.application.liquidation_events import LiquidationEvent

if TYPE_CHECKING:
    from datetime import datetime

    from friendex.adapters.config import Settings
    from friendex.application.interfaces import (
        IFundRepo,
        IPriceRepo,
        ITradeCooldownRepo,
        IUserRepo,
    )
    from friendex.application.lock_manager import LockManager
    from friendex.application.trading_service import TradingService


class LiquidationService:
    """Auto-covers short positions that have rallied past the liquidation threshold."""

    def __init__(
        self,
        *,
        guild_id: str,
        user_repo: IUserRepo,
        price_repo: IPriceRepo,
        fund_repo: IFundRepo,
        cooldown_repo: ITradeCooldownRepo,
        lock_manager: LockManager,
        settings: Settings,
        trading_service: TradingService,
    ) -> None:
        self._guild_id = guild_id
        self._user_repo = user_repo
        self._price_repo = price_repo
        self._fund_repo = fund_repo
        self._cooldown_repo = cooldown_repo
        self._locks = lock_manager
        self._settings = settings
        self._trading = trading_service

    def _lock_key(self, user_id: str) -> str:
        """Return the composite ``"<guild>:<user>"`` lock key (ADR-0001)."""
        return f"{self._guild_id}:{user_id}"

    async def check_and_liquidate_shorts(self, now: datetime) -> list[LiquidationEvent]:
        """Sweep every account; liquidate shorts at-or-above the threshold.

        Returns one :class:`LiquidationEvent` per executed liquidation so
        the Phase 9 task can emit a Discord notification per event. The
        sweep is best-effort: if a short raced to be covered manually
        between the pre-lock scan and the locked re-read, it is silently
        skipped. ``now`` is captured on every emitted event so the
        notifier can attribute the liquidation to the tick that detected
        it.
        """
        threshold = Decimal(str(self._settings.liquidation_threshold))
        events: list[LiquidationEvent] = []

        accounts = await self._user_repo.list_all(self._guild_id)
        for account in accounts:
            if not account.short_positions:
                continue
            holder_id = account.user_id
            # Snapshot the short ids before we touch any locks: the inside-
            # lock re-read may show fewer or different shorts, but we never
            # need to introduce a new target mid-sweep.
            target_ids = list(account.short_positions.keys())
            for target_id in target_ids:
                liquidated = await self._maybe_liquidate(
                    holder_id, target_id, threshold, now
                )
                if liquidated is not None:
                    events.append(liquidated)

        return events

    async def _maybe_liquidate(
        self,
        holder_id: str,
        target_id: str,
        threshold: Decimal,
        now: datetime,
    ) -> LiquidationEvent | None:
        """Acquire the holder+target lock and liquidate iff still at threshold.

        Re-reads both the account and the target's stock INSIDE the lock —
        the pre-lock snapshot used to enumerate candidates may have gone
        stale (the holder may have manually covered, or the price may have
        dropped back below the threshold). Skips silently when the
        condition no longer holds.

        Delegates the actual cover to
        :meth:`TradingService._cover_internal` with ``force=True`` so the
        :class:`PositionFrozen` guard is bypassed (the freeze window
        applies only to user-initiated covers).
        """
        async with self._locks.locked(
            self._lock_key(holder_id), self._lock_key(target_id)
        ):
            holder = await self._user_repo.get(self._guild_id, holder_id)
            if holder is None:
                return None
            short = holder.short_positions.get(target_id)
            if short is None:
                return None
            stock = await self._price_repo.get(self._guild_id, target_id)
            if stock is None:
                return None

            trigger_price = short.entry_price * threshold
            if stock.current < trigger_price:
                return None

            entry_price = short.entry_price
            shares = short.shares

            result = await self._trading._cover_internal(
                holder_id, target_id, shares, force=True
            )

        return LiquidationEvent(
            guild_id=self._guild_id,
            holder_id=holder_id,
            target_id=target_id,
            shares=shares,
            entry_price=entry_price,
            exit_price=result.price_per_share,
            collateral_returned=result.released_cash + result.released_fund,
            pnl=result.pnl,
            timestamp=now,
        )
