"""Read-only stats / leaderboard / price-history use cases (Phase 8d).

:class:`StatsService` mediates between the ``/trending``, ``/mystats``,
``/price``, and ``/mystock`` slash commands (Phase 11 cogs) and the
persistence ports. **Every method here is read-only — no locks, no writes.**
Concurrent ticks or trades landing mid-read are tolerated; the worst case is
a slightly-stale leaderboard entry or a 24-hour high/low that misses the
most recent tick by a few milliseconds.

The service is a pure orchestrator over two domain pure-functions
(:func:`friendex.domain.activity.calculate_trending_score` /
:func:`~friendex.domain.activity.get_engagement_tier`) plus a 24-hour
price-history window read via :meth:`IPriceRepo.get_history`. No math here —
the activity weighting, tier cuts, and the dynamic 24h high/low decision
(§Open-Q9 in ``docs/02-target-architecture.md``) all live in the domain
layer or the persistence boundary.

**Guild scoping (ADR-0001 / Phase 8a digest).** ``guild_id`` is a constructor
argument captured once as ``self._guild_id``; domain models stay
guild-agnostic. Read methods scope every repository call to
``self._guild_id``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from friendex.application.snapshot_models import (
    PriceStats,
    TrendingEntry,
    UserStats,
)
from friendex.domain.activity import calculate_trending_score, get_engagement_tier

if TYPE_CHECKING:
    from decimal import Decimal

    from friendex.adapters.config import Settings
    from friendex.application.interfaces import IPriceRepo, IUserRepo

# Default top-N for the ``/trending`` leaderboard; overridable via kwarg.
_DEFAULT_TRENDING_LIMIT = 15
# Width of the rolling price-stats window per §Open-Q9.
_PRICE_STATS_WINDOW = timedelta(hours=24)


class StatsService:
    """Read-only trending / activity / price-stats use cases."""

    def __init__(
        self,
        *,
        guild_id: str,
        user_repo: IUserRepo,
        price_repo: IPriceRepo,
        settings: Settings,
    ) -> None:
        self._guild_id = guild_id
        self._user_repo = user_repo
        self._price_repo = price_repo
        self._settings = settings

    # -- internal helpers ---------------------------------------------------

    async def _current_price(self, user_id: str) -> Decimal | None:
        """Return the target's current stock price or ``None`` if no stock row."""
        stock = await self._price_repo.get(self._guild_id, user_id)
        return stock.current if stock is not None else None

    # -- public read use cases (lockless) ----------------------------------

    async def trending_snapshot(
        self, limit: int = _DEFAULT_TRENDING_LIMIT
    ) -> list[TrendingEntry]:
        """Return the top-``limit`` trending users, sorted DESC by score.

        Users whose today-bucket score is exactly zero are filtered out — they
        are not interesting on a leaderboard and clutter the embed. Ties keep
        the underlying-sort order (stable Python sort), which is acceptable
        because both colliding entries share the same numeric rank in the
        domain function anyway.

        The default ``limit`` of 15 matches the Phase 8d spec; callers may
        override it for compact embeds.
        """
        accounts = await self._user_repo.list_all(self._guild_id)
        scored = [
            (calculate_trending_score(account.today), account) for account in accounts
        ]
        scored = [pair for pair in scored if pair[0] > 0.0]
        scored.sort(key=lambda pair: pair[0], reverse=True)

        top = scored[:limit]
        entries: list[TrendingEntry] = []
        for rank, (score, account) in enumerate(top, start=1):
            price = await self._current_price(account.user_id)
            entries.append(
                TrendingEntry(
                    rank=rank,
                    user_id=account.user_id,
                    score=score,
                    current_price=price if price is not None else _zero_price(),
                )
            )
        return entries

    async def user_stats(self, user_id: str) -> UserStats | None:
        """Return ``user_id``'s ``/mystats`` snapshot or ``None`` if absent.

        The engagement tier is computed against every account in the guild
        (the same population the leaderboard uses), per the Phase 4 contract
        of :func:`get_engagement_tier`.
        """
        target = await self._user_repo.get(self._guild_id, user_id)
        if target is None:
            return None

        all_accounts = await self._user_repo.list_all(self._guild_id)
        all_scores = [
            calculate_trending_score(account.today) for account in all_accounts
        ]
        my_score = calculate_trending_score(target.today)
        tier = get_engagement_tier(my_score, all_scores)

        return UserStats(
            user_id=target.user_id,
            trending_score=my_score,
            engagement_tier=tier,
            last_activity=target.last_activity,
        )

    async def get_price_stats(self, user_id: str) -> PriceStats | None:
        """Return ``user_id``'s ``/price`` / ``/mystock`` snapshot, or ``None``.

        ``high_24h`` and ``low_24h`` are computed **dynamically** from the
        rolling 24-hour history window (per §Open-Q9), via
        :meth:`IPriceRepo.get_history` with ``since=now - 24h``. The boundary
        is inclusive (``>=``), matching the adapter + fake-repo semantics
        documented in the Phase-8-fakes digest.

        When the 24-hour window is empty (brand-new stock, history pruned, no
        ticks in 24 h) both fall back to the current price so the embed
        builder never has to handle ``None``.
        """
        stock = await self._price_repo.get(self._guild_id, user_id)
        if stock is None:
            return None

        since = datetime.now(tz=UTC) - _PRICE_STATS_WINDOW
        history = await self._price_repo.get_history(
            self._guild_id, user_id, since=since
        )
        prices_in_window = [point.price for point in history]
        if prices_in_window:
            high = max(prices_in_window)
            low = min(prices_in_window)
        else:
            high = stock.current
            low = stock.current

        return PriceStats(
            user_id=stock.user_id,
            current=stock.current,
            high_24h=high,
            low_24h=low,
            all_time_high=stock.all_time_high,
        )


def _zero_price() -> Decimal:
    """Return a zero-quantised :class:`Decimal` for the missing-stock fallback.

    ``Decimal`` is imported lazily here so the ``TYPE_CHECKING`` import block
    stays the sole top-level import of the symbol. The fallback is hit only
    when a leaderboard user has a :class:`UserAccount` but no :class:`Stock`
    row — a transient state that should be vanishingly rare in production
    (every account upserted by ``TradingService`` also upserts a stock).
    """
    from decimal import Decimal

    return Decimal("0.00")
