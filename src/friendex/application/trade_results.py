"""Frozen result dataclasses returned by :class:`TradingService` use cases.

Each public method on :class:`~friendex.application.trading_service.TradingService`
returns one of these. They are the application-layer DTO contract the Phase 10
embed builders consume — they carry *everything* the embed needs (before/after
prices, total cost, post-trade balance, post-trade position) so the builder
never has to re-read state.

All fields are :class:`~decimal.Decimal` for money/price (Phase 3.1 invariant)
and the dataclasses are ``frozen=True`` — once returned, a result is a stable
snapshot of the trade outcome.

The aggregate snapshots (:attr:`BuyResult.position_after`, etc.) are typed as
the matching domain dataclasses. A position that was *fully closed* (sell or
cover dropping shares to zero) is represented by ``position_after = None`` —
the trading service deletes the position record entirely in that case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decimal import Decimal

    from friendex.domain.models import LongPosition, ShortPosition


@dataclass(frozen=True)
class BuyResult:
    """Outcome of a successful :meth:`TradingService.buy` call.

    ``position_after`` is the post-trade long position (a brand-new
    :class:`LongPosition` on an opening trade, or a replaced one with refreshed
    ``shares`` + ``avg_entry`` when adding to an existing position).
    """

    buyer_id: str
    target_id: str
    shares: int
    price_per_share: Decimal
    total_cost: Decimal
    old_price: Decimal
    new_price: Decimal
    new_cash_balance: Decimal
    position_after: LongPosition


@dataclass(frozen=True)
class SellResult:
    """Outcome of a successful :meth:`TradingService.sell` call.

    ``position_after`` is ``None`` when the sell fully closed the position
    (shares dropped to zero) — the service deletes the long-position record in
    that case. Otherwise it is the replaced :class:`LongPosition` with
    decremented ``shares``.
    """

    seller_id: str
    target_id: str
    shares: int
    price_per_share: Decimal
    total_revenue: Decimal
    old_price: Decimal
    new_price: Decimal
    new_cash_balance: Decimal
    position_after: LongPosition | None


@dataclass(frozen=True)
class ShortResult:
    """Outcome of a successful :meth:`TradingService.short` call.

    The collateral split mirrors the original spec: ``locked_cash`` comes from
    the shorter's cash balance first; ``locked_fund`` then draws from at most
    50% of their personal hedge-fund balance to cover whatever notional remains.
    """

    shorter_id: str
    target_id: str
    shares: int
    price_per_share: Decimal
    notional: Decimal
    locked_cash: Decimal
    locked_fund: Decimal
    old_price: Decimal
    new_price: Decimal
    new_cash_balance: Decimal
    new_fund_balance: Decimal
    position_after: ShortPosition


@dataclass(frozen=True)
class CoverResult:
    """Outcome of a successful :meth:`TradingService.cover` call.

    ``pnl`` is signed: positive when the cover price is below entry (profit),
    negative when above (loss). Profit is credited to cash on top of the
    released collateral; a loss is implicitly absorbed by the released
    collateral itself (the released amount is the same proportion of the
    locked amount regardless of P&L direction).

    ``position_after`` is ``None`` when the cover closed the entire short
    position (shares dropped to zero) — the service deletes the short-position
    record in that case.
    """

    coverer_id: str
    target_id: str
    shares: int
    price_per_share: Decimal
    cost: Decimal
    pnl: Decimal
    released_cash: Decimal
    released_fund: Decimal
    old_price: Decimal
    new_price: Decimal
    new_cash_balance: Decimal
    new_fund_balance: Decimal
    position_after: ShortPosition | None
