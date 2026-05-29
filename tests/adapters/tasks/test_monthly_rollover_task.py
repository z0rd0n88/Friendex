"""Behavioural tests for :class:`MonthlyRolloverTask` (Phase 9 AC8 + Wave 1 #82 C3/H7).

The task runs every hour but acts on a guild only when the persisted
:attr:`SystemState.last_monthly_rollover` is older than the current UTC month.
The Phase 14 day+hour gate is replaced by a durable state field so a missed
fire (process crash, transient failure mid-sweep) is replayed on the next tick
instead of silently skipping a month.

When the task acts for a guild it calls, in order:

1. :meth:`PortfolioService.capture_month_start_net_worth` — snapshots every
   account's net worth as the month's baseline.
2. :meth:`FundService.accrue_apy(now=...)` — credits monthly APY accrual.

Both calls are wrapped independently in :meth:`BackgroundTask._safe_run` so a
portfolio failure does not block fund accrual on the same guild, and a failure
on one guild does not abort the sweep over the others. The state field
``last_monthly_rollover`` is only advanced for a guild once BOTH calls
succeed; otherwise the next tick replays only the failed guilds.

Acceptance criteria:

* **M1** — fresh guild (no state row, or ``last_monthly_rollover is None``):
  both services are called and the state is seeded.
* **M2** — second tick within the same UTC month: no-op.
* **M3** — first tick of a new UTC month: services are called again exactly
  once and the state is advanced.
* **M4** — declared cadence is 1 hour.
* **M5** — service ordering: portfolio BEFORE fund.
* **M6** — service exception on portfolio: state is NOT advanced — next tick
  replays that guild only. Fund accrual MUST NOT run when portfolio failed.
* **M7** — service exception on fund: portfolio ran but state is NOT advanced
  — next tick replays that guild's fund accrual (portfolio is retry-safe).
* **M8** — per-guild isolation: a portfolio exception for guild A does not
  abort guild B's processing, and the exception does NOT propagate out of
  ``_run``.
* **M9** — per-guild isolation on fund: a fund exception for guild A does not
  abort guild B's processing, and the exception does NOT propagate out of
  ``_run``.
* **M10** — multi-guild mid-sweep failure replay: when guild B's services
  fail mid-sweep, the next tick replays only guild B; guild A is skipped
  because its state was already advanced.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from freezegun import freeze_time

from friendex.adapters.tasks.monthly_rollover_task import MonthlyRolloverTask
from friendex.application.interfaces import SystemState

if TYPE_CHECKING:
    from friendex.application.fund_service import FundService
    from friendex.application.portfolio_service import PortfolioService
    from tests.application.fakes.fake_repos import FakeSystemStateRepo


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


async def test_monthly_rollover_fires_on_fresh_guild(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """M1: a guild with no state row fires on the first tick and seeds state."""
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
        system_state_repo=fake_system_state_repo,
    )

    with freeze_time("2026-06-15 12:30:00", tz_offset=0):
        await task._run()

    port.capture_month_start_net_worth.assert_awaited_once()
    fund.accrue_apy.assert_awaited_once()
    state = await fake_system_state_repo.get(GUILD)
    assert state is not None
    assert state.last_monthly_rollover == date(2026, 6, 1)


async def test_monthly_rollover_no_op_within_same_utc_month(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """M2: a second tick within the same UTC month does NOT call the services."""
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
        system_state_repo=fake_system_state_repo,
    )

    with freeze_time("2026-06-01 00:00:00", tz_offset=0):
        await task._run()
    with freeze_time("2026-06-30 23:59:00", tz_offset=0):
        await task._run()

    port.capture_month_start_net_worth.assert_awaited_once()
    fund.accrue_apy.assert_awaited_once()


async def test_monthly_rollover_fires_again_in_new_month(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """M3: a tick in the next UTC month fires again exactly once."""
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
        system_state_repo=fake_system_state_repo,
    )

    # First fire of June.
    with freeze_time("2026-06-01 00:00:00", tz_offset=0):
        await task._run()
    # Within same month — no-op.
    with freeze_time("2026-06-30 23:59:00", tz_offset=0):
        await task._run()
    # Cross to July — fires.
    with freeze_time("2026-07-01 00:30:00", tz_offset=0):
        await task._run()
    # Within July — no-op.
    with freeze_time("2026-07-15 12:00:00", tz_offset=0):
        await task._run()

    assert port.capture_month_start_net_worth.await_count == 2
    assert fund.accrue_apy.await_count == 2


def test_monthly_rollover_cadence_is_one_hour() -> None:
    """M4: declared cadence is 1 hour."""
    assert MonthlyRolloverTask.interval_hours == 1
    assert MonthlyRolloverTask.interval_minutes == 0


async def test_monthly_rollover_calls_portfolio_before_fund(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """M5: ``capture_month_start_net_worth`` runs BEFORE ``accrue_apy``."""
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
        system_state_repo=fake_system_state_repo,
    )

    with freeze_time("2026-06-01 00:30:00", tz_offset=0):
        await task._run()

    assert call_order == ["portfolio", "fund"]


async def test_monthly_rollover_state_not_advanced_on_portfolio_failure(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """M6: portfolio fails → state stays unadvanced AND fund accrual is skipped.

    Reasoning: ``accrue_apy`` requires the month-start baseline written by
    ``capture_month_start_net_worth``. If the baseline write failed we must
    not credit APY against an inflated (stale) baseline. The exception is
    isolated by ``_safe_run`` so the next guild is processed normally; the
    failing guild is replayed on the next tick.
    """
    port = MagicMock()
    port.capture_month_start_net_worth = AsyncMock(side_effect=RuntimeError("boom"))
    fund = MagicMock()
    fund.accrue_apy = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = MonthlyRolloverTask(
        portfolio_service_factory=_portfolio_factory({GUILD: port}),
        fund_service_factory=_fund_factory({GUILD: fund}),
        iter_guild_ids=iter_guilds,
        system_state_repo=fake_system_state_repo,
    )

    # Must NOT raise.
    with freeze_time("2026-06-01 00:30:00", tz_offset=0):
        await task._run()

    port.capture_month_start_net_worth.assert_awaited_once()
    # Fund accrual MUST be skipped: portfolio failed.
    fund.accrue_apy.assert_not_awaited()
    state = await fake_system_state_repo.get(GUILD)
    assert state is None or state.last_monthly_rollover is None


async def test_monthly_rollover_state_not_advanced_on_fund_failure(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """M7: fund fails → ``last_monthly_rollover`` stays unadvanced.

    Even though portfolio already ran, the spec mandates the rollover field
    is only advanced after BOTH services succeed.

    PR #89 review L-1 refinement: ``last_portfolio_capture`` advances
    immediately after the portfolio call succeeds, so the next-tick replay
    skips portfolio and re-runs only fund accrual.
    """
    port = MagicMock()
    port.capture_month_start_net_worth = AsyncMock(return_value=None)
    fund = MagicMock()
    fund.accrue_apy = AsyncMock(side_effect=RuntimeError("nope"))

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = MonthlyRolloverTask(
        portfolio_service_factory=_portfolio_factory({GUILD: port}),
        fund_service_factory=_fund_factory({GUILD: fund}),
        iter_guild_ids=iter_guilds,
        system_state_repo=fake_system_state_repo,
    )

    # Must NOT raise.
    with freeze_time("2026-06-01 00:30:00", tz_offset=0):
        await task._run()

    port.capture_month_start_net_worth.assert_awaited_once()
    fund.accrue_apy.assert_awaited_once()
    state = await fake_system_state_repo.get(GUILD)
    # Rollover marker NOT advanced (fund failed) — but the portfolio marker
    # IS advanced so the next tick skips the now-redundant portfolio call.
    assert state is not None
    assert state.last_monthly_rollover is None
    assert state.last_portfolio_capture == date(2026, 6, 1)


async def test_monthly_rollover_isolates_portfolio_exception_per_guild(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """M8: a portfolio failure in guild A does not abort guild B's processing."""
    port_a = MagicMock()
    port_a.capture_month_start_net_worth = AsyncMock(side_effect=RuntimeError("a-boom"))
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
        system_state_repo=fake_system_state_repo,
    )

    # Must NOT raise.
    with freeze_time("2026-06-01 00:30:00", tz_offset=0):
        await task._run()

    # g1 fund accrual skipped because portfolio failed.
    fund_a.accrue_apy.assert_not_awaited()
    # g2 processed normally.
    port_b.capture_month_start_net_worth.assert_awaited_once()
    fund_b.accrue_apy.assert_awaited_once()

    # g1 state not advanced; g2 state advanced.
    s1 = await fake_system_state_repo.get("g1")
    s2 = await fake_system_state_repo.get("g2")
    assert s1 is None or s1.last_monthly_rollover is None
    assert s2 is not None
    assert s2.last_monthly_rollover == date(2026, 6, 1)


async def test_monthly_rollover_isolates_fund_exception_per_guild(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """M9: a fund failure in guild A does not abort guild B's processing."""
    port_a = MagicMock()
    port_a.capture_month_start_net_worth = AsyncMock(return_value=None)
    port_b = MagicMock()
    port_b.capture_month_start_net_worth = AsyncMock(return_value=None)
    fund_a = MagicMock()
    fund_a.accrue_apy = AsyncMock(side_effect=RuntimeError("a-fund-boom"))
    fund_b = MagicMock()
    fund_b.accrue_apy = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return ["g1", "g2"]

    task = MonthlyRolloverTask(
        portfolio_service_factory=_portfolio_factory({"g1": port_a, "g2": port_b}),
        fund_service_factory=_fund_factory({"g1": fund_a, "g2": fund_b}),
        iter_guild_ids=iter_guilds,
        system_state_repo=fake_system_state_repo,
    )

    # Must NOT raise.
    with freeze_time("2026-06-01 00:30:00", tz_offset=0):
        await task._run()

    # Both guilds saw portfolio.
    port_a.capture_month_start_net_worth.assert_awaited_once()
    port_b.capture_month_start_net_worth.assert_awaited_once()
    # g1 fund raised; g2 fund ran clean.
    fund_a.accrue_apy.assert_awaited_once()
    fund_b.accrue_apy.assert_awaited_once()

    # g1 rollover marker NOT advanced (fund failed); the portfolio marker IS
    # advanced so the next tick skips the portfolio re-run (L-1 fix-up).
    # g2 fully advanced.
    s1 = await fake_system_state_repo.get("g1")
    s2 = await fake_system_state_repo.get("g2")
    assert s1 is not None
    assert s1.last_monthly_rollover is None
    assert s1.last_portfolio_capture == date(2026, 6, 1)
    assert s2 is not None
    assert s2.last_monthly_rollover == date(2026, 6, 1)


async def test_monthly_rollover_replays_only_failed_guild_on_next_tick(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """M10: mid-sweep failure on guild B → next tick replays only B, not A.

    Critical integration test for #82 C3: the durable
    ``last_monthly_rollover`` field is advanced per guild after both services
    succeed; a guild that did not advance is the only one replayed.

    PR #89 review L-1 refinement: B's portfolio is called only ONCE because
    ``last_portfolio_capture`` already advanced on tick 1 (portfolio
    succeeded; only fund failed). The next tick replays only the fund step
    for B.
    """
    port_a = MagicMock()
    port_a.capture_month_start_net_worth = AsyncMock(return_value=None)
    port_b = MagicMock()
    port_b.capture_month_start_net_worth = AsyncMock(return_value=None)
    fund_a = MagicMock()
    fund_a.accrue_apy = AsyncMock(return_value=None)
    fund_b = MagicMock()
    # B fails on first tick, then succeeds on second tick.
    fund_b.accrue_apy = AsyncMock(side_effect=[RuntimeError("b-first-fail"), None])

    async def iter_guilds() -> list[str]:
        return ["g1", "g2"]

    task = MonthlyRolloverTask(
        portfolio_service_factory=_portfolio_factory({"g1": port_a, "g2": port_b}),
        fund_service_factory=_fund_factory({"g1": fund_a, "g2": fund_b}),
        iter_guild_ids=iter_guilds,
        system_state_repo=fake_system_state_repo,
    )

    # First tick — A succeeds, B fails on fund.
    with freeze_time("2026-06-01 00:30:00", tz_offset=0):
        await task._run()
    # Second tick — A is skipped (already advanced); only B's fund is retried.
    with freeze_time("2026-06-01 01:30:00", tz_offset=0):
        await task._run()

    # A: portfolio + fund called ONCE (advanced after the first tick).
    assert port_a.capture_month_start_net_worth.await_count == 1
    assert fund_a.accrue_apy.await_count == 1
    # B: portfolio called ONCE (already captured on tick 1; the L-1 split
    # bookkeeping skips it on tick 2); fund called TWICE (first failed,
    # second succeeded).
    assert port_b.capture_month_start_net_worth.await_count == 1
    assert fund_b.accrue_apy.await_count == 2

    # Both states fully advanced by the end.
    s1 = await fake_system_state_repo.get("g1")
    s2 = await fake_system_state_repo.get("g2")
    assert s1 is not None and s1.last_monthly_rollover == date(2026, 6, 1)
    assert s2 is not None and s2.last_monthly_rollover == date(2026, 6, 1)


async def test_monthly_rollover_replays_only_fund_when_portfolio_already_done(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """M11: a fund-only failure replays ONLY fund on the next tick.

    PR #89 review L-1 fix-up: the task tracks ``last_portfolio_capture`` as a
    separate per-guild marker so a fund failure (with portfolio already
    succeeded for this month) does not re-run the now-idempotent portfolio
    capture on retry. ``last_monthly_rollover`` (the "both succeeded" flag)
    still advances only after both calls land.

    Scenario: first tick — portfolio succeeds, fund raises. Second tick —
    portfolio must NOT run again (already captured this month); fund must
    run and (now succeeding) advance ``last_monthly_rollover``.
    """
    port = MagicMock()
    port.capture_month_start_net_worth = AsyncMock(return_value=None)
    fund = MagicMock()
    # Fund fails on first tick, succeeds on second.
    fund.accrue_apy = AsyncMock(side_effect=[RuntimeError("fund-boom"), None])

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = MonthlyRolloverTask(
        portfolio_service_factory=_portfolio_factory({GUILD: port}),
        fund_service_factory=_fund_factory({GUILD: fund}),
        iter_guild_ids=iter_guilds,
        system_state_repo=fake_system_state_repo,
    )

    # Tick 1 — portfolio ran, fund raised, state not advanced.
    with freeze_time("2026-06-01 00:30:00", tz_offset=0):
        await task._run()
    # Tick 2 — fund retried; portfolio must NOT run again.
    with freeze_time("2026-06-01 01:30:00", tz_offset=0):
        await task._run()

    # Portfolio: ONCE (captured on tick 1, skipped on tick 2 because the
    # split-bookkeeping field already shows June).
    assert port.capture_month_start_net_worth.await_count == 1
    # Fund: TWICE (raised on tick 1, succeeded on tick 2).
    assert fund.accrue_apy.await_count == 2

    # State fully advanced by end.
    state = await fake_system_state_repo.get(GUILD)
    assert state is not None
    assert state.last_monthly_rollover == date(2026, 6, 1)
    assert state.last_portfolio_capture == date(2026, 6, 1)


async def test_monthly_rollover_preserves_daily_and_weekly_state(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """Advancing ``last_monthly_rollover`` does not clobber daily/weekly fields."""
    daily_marker = datetime(2026, 5, 31, 6, 0, tzinfo=UTC)
    weekly_marker = datetime(2026, 5, 25, 6, 0, tzinfo=UTC)
    await fake_system_state_repo.upsert(
        SystemState(
            guild_id=GUILD,
            last_daily_reset=daily_marker,
            last_weekly_reset=weekly_marker,
            last_monthly_rollover=None,
        )
    )

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
        system_state_repo=fake_system_state_repo,
    )

    with freeze_time("2026-06-01 00:30:00", tz_offset=0):
        await task._run()

    state = await fake_system_state_repo.get(GUILD)
    assert state is not None
    assert state.last_daily_reset == daily_marker
    assert state.last_weekly_reset == weekly_marker
    assert state.last_monthly_rollover == date(2026, 6, 1)
