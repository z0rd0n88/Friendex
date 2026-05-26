"""Frozen read-model DTOs returned by the Phase 8d portfolio/stats services.

These dataclasses are the application-layer contract the Phase 10 embed builders
consume for the ``/portfolio``, ``/balance``, ``/trending``, ``/mystats``, and
``/price`` slash commands. They are deliberately **distinct from the domain
models** (:mod:`friendex.domain.models`):

* Domain models (:class:`UserAccount`, :class:`Stock`, :class:`HedgeFund`) are
  the persisted aggregates the rest of the application mutates and round-trips
  through the repositories. They carry every field the persistence layer
  needs (collateral splits, ``ActivityBucket``s, ``DailyProgress``, …).
* Read models here carry **only the fields an embed renders** — pre-computed,
  display-ready, and immutable. The embed builder never re-reads state.

All money/price fields are :class:`~decimal.Decimal` (Phase 3.1 invariant) and
every dataclass is ``frozen=True`` so an embed builder cannot accidentally
mutate a snapshot mid-render.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    from friendex.domain.models import LongPosition, ShortPosition


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Display-ready portfolio for ``/portfolio`` and ``/balance`` embeds.

    ``net_worth`` is the rolled-up valuation computed by
    :func:`~friendex.domain.fund_math.compute_net_worth`; the per-position
    dicts are taken directly from the underlying :class:`UserAccount` so the
    embed builder can list each long/short row without a second repository
    call. ``fund_balance`` is the user's personal hedge-fund cash balance
    (zero if they have not created one).
    """

    user_id: str
    cash_balance: Decimal
    net_worth: Decimal
    month_start_net_worth: Decimal
    fund_balance: Decimal
    long_positions: dict[str, LongPosition] = field(default_factory=dict)
    short_positions: dict[str, ShortPosition] = field(default_factory=dict)


@dataclass(frozen=True)
class TrendingEntry:
    """One row of the ``/trending`` leaderboard.

    ``rank`` is 1-indexed from the top; ``score`` is the raw
    :func:`~friendex.domain.activity.calculate_trending_score` value (unitless
    float, not money — hence not ``Decimal``). ``current_price`` is the
    target's current stock price so the embed can show both the leaderboard
    position and the price next to each entry without a second query.
    """

    rank: int
    user_id: str
    score: float
    current_price: Decimal


@dataclass(frozen=True)
class PriceStats:
    """Display-ready price stats for ``/price`` and ``/mystock`` embeds.

    ``high_24h`` and ``low_24h`` are computed dynamically from the rolling
    24-hour price-history window (per the §Open-Q9 decision in
    ``docs/02-target-architecture.md``); they are not stored fields. When the
    24-hour window is empty (brand-new stock, history pruned, no ticks in
    24 h) both fall back to ``current`` so the embed never has to render a
    ``None`` value. ``all_time_high`` is a stored, monotonically-ratcheted
    field on :class:`Stock`.
    """

    user_id: str
    current: Decimal
    high_24h: Decimal
    low_24h: Decimal
    all_time_high: Decimal


@dataclass(frozen=True)
class UserStats:
    """Display-ready activity stats for ``/mystats`` embeds.

    ``trending_score`` is the user's own
    :func:`~friendex.domain.activity.calculate_trending_score` over their
    ``today`` bucket; ``engagement_tier`` is the percentile-rank bucket
    string from :func:`~friendex.domain.activity.get_engagement_tier`
    (``"Elite"`` / ``"High"`` / ``"Medium"`` / ``"Low"``) computed against
    every other account in the same guild. ``last_activity`` is the raw
    timestamp the embed renders as a relative "last seen" string.
    """

    user_id: str
    trending_score: float
    engagement_tier: str
    last_activity: datetime
