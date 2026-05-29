"""1-hour polling task that fires the month-start rollover with durable replay.

:class:`MonthlyRolloverTask` polls every hour and acts on a guild only when the
persisted :attr:`SystemState.last_monthly_rollover` (a :class:`date`) is older
than the current UTC month's first-of-month marker. On a fresh guild (no state
row, or ``last_monthly_rollover is None``) the very first tick acts and seeds
the field.

When the task acts on a guild it calls, in order:

1. :meth:`PortfolioService.capture_month_start_net_worth` — snapshots every
   account's net worth as the month's baseline (re-running it later in the
   month would overwrite the baseline with an inflated post-APY value,
   skewing P&L; hence the strict ordering).
2. :meth:`FundService.accrue_apy(now=...)` — credits monthly APY accrual.

Both calls are wrapped independently in :meth:`BackgroundTask._safe_run` so a
portfolio failure does not block the next guild's processing, and a fund
failure does not block subsequent guilds either. Per-guild isolation is the
core contract (Wave 1 #82 H7 / #84 H).

**Durable bookkeeping (Wave 1 #82 C3).** Prior behaviour gated firing on
``utcnow().day == 1 and utcnow().hour == 0`` with a 1-hour cadence — a process
restart, transient service failure, or partial sweep at that exact hour
silently skipped a month's APY accrual. The new
:attr:`SystemState.last_monthly_rollover` field is advanced PER GUILD only
after **both** services succeed for that guild. A mid-sweep failure on guild B
means B is replayed on the next tick (every hour), while guild A is skipped
because its state already shows the current month. The field is a
:class:`date` because month-granular bookkeeping reads naturally as
``date(y, m, 1)``; we never compare hour or minute components.

**Why fund accrual is skipped when portfolio failed.** ``accrue_apy`` depends
on the freshly-captured month-start baseline; running it against the stale
prior-month baseline would inflate the credited interest. The fail-stop is
load-bearing — that's why each guild's processing short-circuits on a
portfolio failure rather than logging-and-continuing within the same guild.

**Cadence is declared.** ``interval_hours = 1``; the Phase 14 composition
layer wraps :meth:`_run` in a ``discord.ext.tasks.loop(hours=1)``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from friendex.adapters.tasks.base_task import BackgroundTask
from friendex.application.interfaces import SystemState

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from friendex.application.fund_service import FundService
    from friendex.application.interfaces import ISystemStateRepo
    from friendex.application.portfolio_service import PortfolioService


def _month_start(moment: datetime) -> date:
    """Return the UTC first-of-month :class:`date` for ``moment``.

    Used as the canonical month identifier so the boundary check is a simple
    ``stored != current`` (or ``stored < current`` — both are correct because
    the field is monotonic).
    """
    return date(moment.year, moment.month, 1)


class MonthlyRolloverTask(BackgroundTask):
    """1-hour poll: month-start rollover with durable per-guild bookkeeping."""

    interval_hours = 1

    def __init__(
        self,
        *,
        portfolio_service_factory: Callable[[str], PortfolioService],
        fund_service_factory: Callable[[str], FundService],
        iter_guild_ids: Callable[[], Awaitable[Iterable[str]]],
        system_state_repo: ISystemStateRepo,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._portfolio_factory = portfolio_service_factory
        self._fund_factory = fund_service_factory
        self._iter_guild_ids = iter_guild_ids
        self._state_repo = system_state_repo
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(tz=UTC))

    async def _run(self) -> None:
        """Per-tick body — replay every guild whose stored marker is stale.

        Each guild's full processing block (stale-check + portfolio + fund +
        state advance) is wrapped in :meth:`BackgroundTask._safe_run` so a
        per-guild exception in ANY phase — service call OR state repo IO —
        does not abort the sweep over the other guilds (Wave 1 #82 H7 /
        #84 H). Inside one guild's block, a portfolio exception short-
        circuits before fund accrual runs (load-bearing: ``accrue_apy``
        requires the freshly-captured baseline) and a fund exception
        prevents the state advance — both behaviours fall out naturally
        from straight-line exception propagation inside ``_process_guild``.
        """
        now = self._clock()
        month_marker = _month_start(now)
        for guild_id in await self._iter_guild_ids():
            await self._safe_run(self._process_guild(guild_id, now, month_marker))

    async def _process_guild(
        self, guild_id: str, now: datetime, month_marker: date
    ) -> None:
        """Process one guild: stale-check, portfolio, fund, advance state.

        Order is load-bearing: portfolio capture MUST run before fund accrual
        (the baseline must be fresh before APY is credited) AND both must
        succeed before the durable rollover marker advances (otherwise the
        next tick would skip a guild that owed a retry).
        """
        if not await self._is_stale(guild_id, month_marker):
            return
        portfolio = self._portfolio_factory(guild_id)
        await portfolio.capture_month_start_net_worth()
        fund = self._fund_factory(guild_id)
        await fund.accrue_apy(now=now)
        await self._advance_state(guild_id, month_marker)

    async def _is_stale(self, guild_id: str, month_marker: date) -> bool:
        """Return ``True`` iff the guild's stored marker is older than ``month_marker``.

        A fresh guild (no state row, or ``last_monthly_rollover is None``)
        counts as stale so the first tick seeds the field.
        """
        state = await self._state_repo.get(guild_id)
        if state is None or state.last_monthly_rollover is None:
            return True
        return state.last_monthly_rollover < month_marker

    async def _advance_state(self, guild_id: str, month_marker: date) -> None:
        """Upsert :class:`SystemState` with ``last_monthly_rollover = month_marker``.

        Preserves ``last_daily_reset`` and ``last_weekly_reset`` so the three
        reset clocks stay independent.
        """
        existing = await self._state_repo.get(guild_id)
        new_state = SystemState(
            guild_id=guild_id,
            last_daily_reset=(existing.last_daily_reset if existing else None),
            last_weekly_reset=(existing.last_weekly_reset if existing else None),
            last_monthly_rollover=month_marker,
        )
        await self._state_repo.upsert(new_state)
