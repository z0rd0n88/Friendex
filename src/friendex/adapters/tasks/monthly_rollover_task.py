"""1-hour polling task that fires the month-start rollover on day-1 hour-0.

:class:`MonthlyRolloverTask` polls every hour and fires only when both
``utcnow().day == 1`` and ``utcnow().hour == 0``. The 1-hour cadence plus
the day+hour gate guarantees exactly one fire per UTC month, so the task
needs NO new :class:`SystemState` field for bookkeeping — per the Phase 8e
digest, the two services it calls are both retry-safe.

When the task fires for a guild it calls, in order:

1. :meth:`PortfolioService.capture_month_start_net_worth` — snapshots every
   account's net worth as the month's baseline (re-running it later in the
   month would overwrite the baseline with an inflated post-APY value,
   skewing P&L; hence the strict ordering).
2. :meth:`FundService.accrue_apy(now=...)` — credits monthly APY accrual.

Both calls are wrapped independently in :meth:`BackgroundTask._safe_run` so
a portfolio failure does not block fund accrual and vice versa.

**Cadence is declared.** ``interval_hours = 1``; the Phase 14 composition
layer wraps :meth:`_run` in a ``discord.ext.tasks.loop(hours=1)``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from friendex.adapters.tasks.base_task import BackgroundTask

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from friendex.application.fund_service import FundService
    from friendex.application.portfolio_service import PortfolioService


class MonthlyRolloverTask(BackgroundTask):
    """1-hour poll: month-start rollover on day-1 hour-0 UTC."""

    interval_hours = 1

    def __init__(
        self,
        *,
        portfolio_service_factory: Callable[[str], PortfolioService],
        fund_service_factory: Callable[[str], FundService],
        iter_guild_ids: Callable[[], Awaitable[Iterable[str]]],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._portfolio_factory = portfolio_service_factory
        self._fund_factory = fund_service_factory
        self._iter_guild_ids = iter_guild_ids
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(tz=UTC))

    async def _run(self) -> None:
        """Per-tick body — gate, then run portfolio + fund per guild."""
        now = self._clock()
        if now.day != 1 or now.hour != 0:
            return
        for guild_id in await self._iter_guild_ids():
            portfolio = self._portfolio_factory(guild_id)
            fund = self._fund_factory(guild_id)
            # Order is load-bearing: capture the baseline BEFORE APY accrues
            # so the month-start net worth reflects the pre-accrual value.
            await self._safe_run(portfolio.capture_month_start_net_worth())
            await self._safe_run(fund.accrue_apy(now=now))
