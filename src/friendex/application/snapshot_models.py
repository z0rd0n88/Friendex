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

**Read-only collection types (#84 M, #84 L).**

* :attr:`PortfolioSnapshot.positions` previously typed as ``dict[str, X]``
  let an embed builder mutate the stored aggregate. Re-typing to
  :class:`~collections.abc.Mapping` exposes a read-only structural view; the
  underlying value is still a dict, but the type system rejects ``.pop`` /
  ``__setitem__`` at the boundary.
* :attr:`UserStats.engagement_tier` narrows from ``str`` to
  ``Literal["Elite", "High", "Medium", "Low"]`` so a future typo in a tier
  string is caught by the static type checker rather than only by user-
  visible weird copy.
* :class:`FundInfoResult.fund` embeds a :class:`HedgeFund` whose
  ``investors`` dict is a mutable aggregate. The frozen DTO wraps it in a
  :func:`types.MappingProxyType` snapshot via the new
  :attr:`FundInfoResult.investors_view` field so callers reading the
  investor stakes from the DTO cannot mutate the underlying aggregate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from friendex.domain.activity import EngagementTier

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime
    from decimal import Decimal

    from friendex.domain.models import HedgeFund, LongPosition, ShortPosition

# Re-export the tier literal so callers can ``from friendex.application
# .snapshot_models import EngagementTier`` for symmetry with the existing
# DTO imports (the canonical declaration lives in :mod:`friendex.domain.activity`).
__all__ = [
    "EngagementTier",
    "FundInfoResult",
    "PortfolioSnapshot",
    "PriceStats",
    "TrendingEntry",
    "UserStats",
]


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Display-ready portfolio for ``/portfolio`` and ``/balance`` embeds.

    ``net_worth`` is the rolled-up valuation computed by
    :func:`~friendex.domain.fund_math.compute_net_worth`; the per-position
    :class:`~collections.abc.Mapping` views are taken directly from the
    underlying :class:`UserAccount` so the embed builder can list each
    long/short row without a second repository call. ``fund_balance`` is
    the user's personal hedge-fund cash balance (zero if they have not
    created one).

    **Read-only position views (#84 M).** ``long_positions`` /
    ``short_positions`` are typed :class:`Mapping` (not ``dict``) so embed
    builders cannot accidentally mutate the underlying aggregate. The
    runtime value is still a plain dict — production callers see no
    behavioural change — but ``snapshot.long_positions["new"] = ...`` is
    now a type error.
    """

    user_id: str
    cash_balance: Decimal
    net_worth: Decimal
    month_start_net_worth: Decimal
    fund_balance: Decimal
    long_positions: Mapping[str, LongPosition] = field(default_factory=dict)
    short_positions: Mapping[str, ShortPosition] = field(default_factory=dict)


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
    computed against every other account in the same guild.
    ``last_activity`` is the raw timestamp the embed renders as a relative
    "last seen" string.

    **Tier narrowing (#84 L).** ``engagement_tier`` is
    :class:`Literal["Elite", "High", "Medium", "Low"]` rather than ``str``
    so the contract is statically enforceable end to end — a future
    callsite typo like ``"Eilte"`` surfaces as a type error rather than
    only as wonky user-visible copy. The exhaustive type also enables the
    embed builder to use a ``match`` for colour selection without
    sprinkling ``# type: ignore`` comments on the default branch.
    """

    user_id: str
    trending_score: float
    engagement_tier: EngagementTier
    last_activity: datetime


@dataclass(frozen=True)
class FundInfoResult:
    """Display-ready fund summary for ``/fund info`` and ``/fund create`` embeds.

    Packages the :class:`~friendex.domain.models.HedgeFund` with APY values
    computed by :class:`~friendex.application.fund_service.FundService` so the
    caller (``FundCog``) does not need access to
    :class:`~friendex.adapters.config.Settings` or domain math helpers.
    ``base_apy`` and ``effective_apy`` are ``float`` matching the return type
    of :func:`~friendex.domain.fund_math.compute_effective_apy`.
    ``has_penalty`` is ``True`` when an early-withdrawal penalty is active at
    the time of the read (i.e. ``penalty.penalty_until > now``).

    **Immutability caveat (#84 L).** The DTO is ``frozen=True``, but ``fund``
    is a mutable :class:`HedgeFund` aggregate whose ``investors`` dict an
    embed builder could in principle mutate. To keep the frozen contract
    meaningful, :attr:`investors_view` snapshots
    :attr:`HedgeFund.investors` into a :func:`types.MappingProxyType` at
    DTO construction time — callers that need to inspect investor stakes
    should read ``result.investors_view`` rather than
    ``result.fund.investors``. The ``fund`` field itself remains
    :class:`HedgeFund` (rather than a fresh projection type) so existing
    embed builders that read ``fund.name`` / ``fund.cash_balance`` /
    ``fund.manager_id`` keep working without adapter-side changes;
    introducing a dedicated ``FundProjection`` is a future evolution that
    crosses adapter boundaries and is therefore not in scope for this
    domain-consolidation pass.
    """

    fund: HedgeFund
    base_apy: float
    effective_apy: float
    has_penalty: bool
    investors_view: Mapping[str, Decimal] = field(
        default_factory=lambda: MappingProxyType({})
    )

    @classmethod
    def from_fund(
        cls,
        fund: HedgeFund,
        *,
        base_apy: float,
        effective_apy: float,
        has_penalty: bool,
    ) -> FundInfoResult:
        """Build a :class:`FundInfoResult` wrapping ``fund.investors`` read-only.

        The ``investors`` dict is *copied* into a :class:`MappingProxyType`
        so a later mutation of the original aggregate does not race into
        the frozen DTO snapshot. Service code should prefer this factory
        over the bare constructor to keep the read-only contract sticky.
        """
        return cls(
            fund=fund,
            base_apy=base_apy,
            effective_apy=effective_apy,
            has_penalty=has_penalty,
            investors_view=MappingProxyType(dict(fund.investors)),
        )
