"""Frozen event dataclass emitted by :class:`LiquidationService`.

Each automatic short cover produced by
:meth:`~friendex.application.liquidation_service.LiquidationService.check_and_liquidate_shorts`
returns a :class:`LiquidationEvent` so the Phase 9
:class:`~friendex.adapters.tasks.liquidation_task.LiquidationTask` can render
the corresponding Discord embed/notification without re-reading state.

The event mirrors the post-cover information held by
:class:`~friendex.application.trade_results.CoverResult` but is intentionally
narrowed to the fields the notification path needs:

* ``guild_id`` — the per-guild scope the liquidation belongs to. The Phase 14
  notifier dispatches the embed to ``bot.get_guild(int(guild_id)).system_channel``,
  so the event carries the guild explicitly rather than relying on the task to
  thread it through.
* ``holder_id`` / ``target_id`` — who got liquidated and on whose stock.
* ``shares`` — how many shares were force-covered (always the full short
  size; partial liquidations are not modelled).
* ``entry_price`` — the short position's volume-weighted entry that the
  liquidation closed against.
* ``exit_price`` — the market price at which the cover executed (i.e. the
  current price at the moment the liquidation snapped).
* ``collateral_returned`` — the total of cash + fund collateral that was
  released back to the holder by the cover (sum of
  :attr:`CoverResult.released_cash` + :attr:`CoverResult.released_fund`).
* ``pnl`` — signed P&L of the cover (negative on a loss, which is the
  common case for liquidations triggered by an unfavourable rally).
* ``timestamp`` — the ``now`` passed to
  :meth:`LiquidationService.check_and_liquidate_shorts`, captured so the
  Phase 9 notifier can attribute the event to the tick that detected it.

All fields are :class:`~decimal.Decimal` for money (Phase 3.1 invariant);
the dataclass is ``frozen=True`` — once returned, an event is an immutable
snapshot suitable for downstream serialisation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal


@dataclass(frozen=True)
class LiquidationEvent:
    """An automatic short cover emitted by :class:`LiquidationService`."""

    guild_id: str
    holder_id: str
    target_id: str
    shares: int
    entry_price: Decimal
    exit_price: Decimal
    collateral_returned: Decimal
    pnl: Decimal
    timestamp: datetime
