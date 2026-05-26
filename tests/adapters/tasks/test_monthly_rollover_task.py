"""Behavioural tests for :class:`MonthlyRolloverTask` (Phase 9 AC8).

The task runs every hour but fires only when both ``utcnow().day == 1`` and
``utcnow().hour == 0``. When it fires for a guild, it calls (in order):

1. :meth:`PortfolioService.capture_month_start_net_worth` — snapshots every
   account's net worth as the month's baseline.
2. :meth:`FundService.accrue_apy(now=...)` — credits monthly APY accrual to
   every personal fund in the guild.

Per the Phase 8e digest, ``accrue_apy`` is retry-safe; the day+hour gate
plus the 1-hour cadence ensures the task fires at most once per UTC month
without needing a new :class:`SystemState` field.

Acceptance criteria:

* **M1** — at ``2026-06-01 00:00`` UTC: both services are called per guild.
* **M2** — at ``2026-06-01 01:00`` UTC (day-1 but past hour 0): no-op.
* **M3** — at ``2026-06-15 00:00`` UTC (hour 0 but not day 1): no-op.
* **M4** — at ``2026-06-02 00:00`` UTC (day 2 hour 0): no-op.
* **M5** — service ordering: ``capture_month_start_net_worth`` BEFORE
  ``accrue_apy``.
* **M6** — declared cadence is 1 hour.
* **M7** — service exception on portfolio does not block fund accrual (each
  call is wrapped independently); both are wrapped under ``_safe_run``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from freezegun import freeze_time

from friendex.adapters.tasks.monthly_rollover_task import MonthlyRolloverTask

if TYPE_CHECKING:
    from datetime import datetime

    from friendex.application.fund_service import FundService
    from friendex.application.portfolio_service import PortfolioService


GUILD = "g1"


def _portfolio_factory(
    services: dict[str, PortfolioService],
) -> object:
    def factory(guild_id: str) -> PortfolioService:
        return services[guild_id]

    return factory


def _fund_factory(services: dict[str, FundService]) -> object:
    def factory(guild_id: str) -> FundService:
        return services[guild_id]

    return factory


async def test_monthly_rollover_fires_on_day_one_hour_zero() -> None:
    """M1: on 1st of month at hour 0, both services are called per guild."""
    port = MagicMock()
    port.capture_month_start_net_worth = AsyncMock(return_value=None)
    fund = MagicMock()
    fund.accrue_apy = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = MonthlyRolloverTask(
        portfolio_service_factory=_portfolio_factory({GUILD: port}),
        fund_service_factory=_fund_factory({GUILD: fund}),
        iter_guild_ids=iter_guilds,
    )

    with freeze_time("2026-06-01 00:30:00", tz_offset=0):
        await task._run()

    port.capture_month_start_net_worth.assert_awaited_once()
    fund.accrue_apy.assert_awaited_once()


async def test_monthly_rollover_no_op_when_day_one_but_past_hour_zero() -> None:
    """M2: day 1 but hour != 0 must not fire (1-hour cadence guards us)."""
    port = MagicMock()
    port.capture_month_start_net_worth = AsyncMock(return_value=None)
    fund = MagicMock()
    fund.accrue_apy = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = MonthlyRolloverTask(
        portfolio_service_factory=_portfolio_factory({GUILD: port}),
        fund_service_factory=_fund_factory({GUILD: fund}),
        iter_guild_ids=iter_guilds,
    )

    with freeze_time("2026-06-01 01:30:00", tz_offset=0):
        await task._run()

    port.capture_month_start_net_worth.assert_not_awaited()
    fund.accrue_apy.assert_not_awaited()


async def test_monthly_rollover_no_op_when_hour_zero_but_not_day_one() -> None:
    """M3: hour 0 but day != 1 is a no-op."""
    port = MagicMock()
    port.capture_month_start_net_worth = AsyncMock(return_value=None)
    fund = MagicMock()
    fund.accrue_apy = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = MonthlyRolloverTask(
        portfolio_service_factory=_portfolio_factory({GUILD: port}),
        fund_service_factory=_fund_factory({GUILD: fund}),
        iter_guild_ids=iter_guilds,
    )

    with freeze_time("2026-06-15 00:30:00", tz_offset=0):
        await task._run()

    port.capture_month_start_net_worth.assert_not_awaited()
    fund.accrue_apy.assert_not_awaited()


async def test_monthly_rollover_no_op_when_day_two_hour_zero() -> None:
    """M4: even at hour 0, day 2 is past the rollover gate."""
    port = MagicMock()
    port.capture_month_start_net_worth = AsyncMock(return_value=None)
    fund = MagicMock()
    fund.accrue_apy = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = MonthlyRolloverTask(
        portfolio_service_factory=_portfolio_factory({GUILD: port}),
        fund_service_factory=_fund_factory({GUILD: fund}),
        iter_guild_ids=iter_guilds,
    )

    with freeze_time("2026-06-02 00:30:00", tz_offset=0):
        await task._run()

    port.capture_month_start_net_worth.assert_not_awaited()
    fund.accrue_apy.assert_not_awaited()


async def test_monthly_rollover_calls_portfolio_before_fund() -> None:
    """M5: ``capture_month_start_net_worth`` runs BEFORE ``accrue_apy``.

    Order matters: the month-start baseline must be captured against the
    pre-accrual net worth, otherwise the freshly-accrued APY would inflate
    the baseline and skew the month's P&L attribution.
    """
    call_order: list[str] = []

    port = MagicMock()

    async def port_call() -> None:
        call_order.append("portfolio")

    port.capture_month_start_net_worth = AsyncMock(side_effect=port_call)

    fund = MagicMock()

    async def fund_call(*, now: datetime) -> None:
        call_order.append("fund")

    fund.accrue_apy = AsyncMock(side_effect=fund_call)

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = MonthlyRolloverTask(
        portfolio_service_factory=_portfolio_factory({GUILD: port}),
        fund_service_factory=_fund_factory({GUILD: fund}),
        iter_guild_ids=iter_guilds,
    )

    with freeze_time("2026-06-01 00:30:00", tz_offset=0):
        await task._run()

    assert call_order == ["portfolio", "fund"]


def test_monthly_rollover_cadence_is_one_hour() -> None:
    """M6: declared cadence is 1 hour."""
    assert MonthlyRolloverTask.interval_hours == 1
    assert MonthlyRolloverTask.interval_minutes == 0


async def test_monthly_rollover_swallows_service_exception() -> None:
    """M7: a portfolio failure does NOT block fund accrual; both wrapped."""
    port = MagicMock()
    port.capture_month_start_net_worth = AsyncMock(side_effect=RuntimeError("nope"))
    fund = MagicMock()
    fund.accrue_apy = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = MonthlyRolloverTask(
        portfolio_service_factory=_portfolio_factory({GUILD: port}),
        fund_service_factory=_fund_factory({GUILD: fund}),
        iter_guild_ids=iter_guilds,
    )

    with freeze_time("2026-06-01 00:30:00", tz_offset=0):
        # Must NOT raise.
        await task._run()

    port.capture_month_start_net_worth.assert_awaited_once()
    # Fund still accrues even after portfolio failure.
    fund.accrue_apy.assert_awaited_once()


async def test_monthly_rollover_fans_out_per_guild() -> None:
    """Both services are invoked for every guild in iter_guild_ids."""
    port_a = MagicMock()
    port_a.capture_month_start_net_worth = AsyncMock(return_value=None)
    port_b = MagicMock()
    port_b.capture_month_start_net_worth = AsyncMock(return_value=None)
    fund_a = MagicMock()
    fund_a.accrue_apy = AsyncMock(return_value=None)
    fund_b = MagicMock()
    fund_b.accrue_apy = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return ["g1", "g2"]

    task = MonthlyRolloverTask(
        portfolio_service_factory=_portfolio_factory({"g1": port_a, "g2": port_b}),
        fund_service_factory=_fund_factory({"g1": fund_a, "g2": fund_b}),
        iter_guild_ids=iter_guilds,
    )

    with freeze_time("2026-06-01 00:30:00", tz_offset=0):
        await task._run()

    port_a.capture_month_start_net_worth.assert_awaited_once()
    port_b.capture_month_start_net_worth.assert_awaited_once()
    fund_a.accrue_apy.assert_awaited_once()
    fund_b.accrue_apy.assert_awaited_once()
