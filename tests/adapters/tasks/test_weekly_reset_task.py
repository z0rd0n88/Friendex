"""Behavioural tests for :class:`WeeklyResetTask` (Phase 9 AC7).

Same shape as :class:`DailyResetTask` but keyed on ISO week boundaries
(``isocalendar().year + isocalendar().week``) so the fire-exactly-once
semantics work across both year and ISO-week boundaries.

Acceptance criteria:

* **W1** — fresh guild: first tick acts, seeds ``last_weekly_reset``.
* **W2** — second tick within the same ISO week: no-op.
* **W3** — first tick of the next ISO week: acts again exactly once.
* **W4** — cross year boundary (week 53 → week 1): fires exactly once.
* **W5** — declared cadence is 1 minute.
* **W6** — service failure leaves state unadvanced.
* **W7** — daily reset state is preserved (only ``last_weekly_reset`` is touched).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from freezegun import freeze_time

from friendex.adapters.tasks.weekly_reset_task import WeeklyResetTask
from friendex.application.interfaces import SystemState

if TYPE_CHECKING:
    from friendex.application.activity_service import ActivityService
    from tests.application.fakes.fake_repos import FakeSystemStateRepo


GUILD = "g1"


def _factory(services: dict[str, ActivityService]) -> object:
    def factory(guild_id: str) -> ActivityService:
        return services[guild_id]

    return factory


async def test_weekly_reset_first_tick_on_fresh_guild(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """W1: fresh guild acts on the first tick and seeds last_weekly_reset."""
    svc = MagicMock()
    svc.reset_week_buckets = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = WeeklyResetTask(
        service_factory=_factory({GUILD: svc}),
        iter_guild_ids=iter_guilds,
        system_state_repo=fake_system_state_repo,
    )

    with freeze_time("2026-05-25 12:00:00", tz_offset=0):
        await task._run()

    svc.reset_week_buckets.assert_awaited_once()
    state = await fake_system_state_repo.get(GUILD)
    assert state is not None
    assert state.last_weekly_reset is not None


async def test_weekly_reset_no_op_within_same_iso_week(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """W2: a tick within the same ISO week as last_weekly_reset is a no-op."""
    svc = MagicMock()
    svc.reset_week_buckets = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = WeeklyResetTask(
        service_factory=_factory({GUILD: svc}),
        iter_guild_ids=iter_guilds,
        system_state_repo=fake_system_state_repo,
    )

    # 2026-05-25 is a Monday — ISO week 22 of 2026. 2026-05-30 is Saturday of
    # the same week.
    with freeze_time("2026-05-25 12:00:00", tz_offset=0):
        await task._run()
    with freeze_time("2026-05-30 12:00:00", tz_offset=0):
        await task._run()

    svc.reset_week_buckets.assert_awaited_once()


async def test_weekly_reset_fires_again_on_next_iso_week(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """W3: a tick in the next ISO week fires again exactly once."""
    svc = MagicMock()
    svc.reset_week_buckets = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = WeeklyResetTask(
        service_factory=_factory({GUILD: svc}),
        iter_guild_ids=iter_guilds,
        system_state_repo=fake_system_state_repo,
    )

    # 2026-05-25 (Mon) is ISO week 22; 2026-06-01 (Mon) is ISO week 23.
    with freeze_time("2026-05-25 12:00:00", tz_offset=0):
        await task._run()
    with freeze_time("2026-05-31 23:59:00", tz_offset=0):
        await task._run()  # still week 22 — no-op
    with freeze_time("2026-06-01 00:01:00", tz_offset=0):
        await task._run()  # week 23 — fires
    with freeze_time("2026-06-01 12:00:00", tz_offset=0):
        await task._run()  # still week 23 — no-op

    assert svc.reset_week_buckets.await_count == 2


async def test_weekly_reset_fires_across_iso_year_boundary(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """W4: the week→year-rollover boundary fires exactly once.

    2025 has 52 ISO weeks; 2025-12-29 is week 1 of ISO year 2026. So a tick
    on 2025-12-28 (ISO week 52 of 2025) followed by 2025-12-29 (ISO week 1
    of 2026) must fire again — testing that we key on (iso_year, iso_week)
    not on iso_week alone.
    """
    svc = MagicMock()
    svc.reset_week_buckets = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = WeeklyResetTask(
        service_factory=_factory({GUILD: svc}),
        iter_guild_ids=iter_guilds,
        system_state_repo=fake_system_state_repo,
    )

    # Sanity: confirm the ISO-week values we're relying on.
    assert datetime(2025, 12, 28, tzinfo=UTC).isocalendar().week == 52
    assert datetime(2025, 12, 28, tzinfo=UTC).isocalendar().year == 2025
    assert datetime(2025, 12, 29, tzinfo=UTC).isocalendar().week == 1
    assert datetime(2025, 12, 29, tzinfo=UTC).isocalendar().year == 2026

    with freeze_time("2025-12-28 12:00:00", tz_offset=0):
        await task._run()
    with freeze_time("2025-12-29 00:01:00", tz_offset=0):
        await task._run()

    assert svc.reset_week_buckets.await_count == 2


def test_weekly_reset_cadence_is_one_minute() -> None:
    """W5: declared cadence is 1 minute."""
    assert WeeklyResetTask.interval_minutes == 1
    assert WeeklyResetTask.interval_hours == 0


async def test_weekly_reset_state_not_advanced_on_service_failure(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """W6: a failing service call does NOT advance ``last_weekly_reset``.

    Each per-guild call is wrapped under ``_safe_run`` so the exception is
    isolated (does NOT propagate). Because ``_advance_state`` is only called
    after a successful reset, the failure path leaves the state unchanged
    and the next tick retries.
    """
    svc = MagicMock()
    svc.reset_week_buckets = AsyncMock(side_effect=RuntimeError("oops"))

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = WeeklyResetTask(
        service_factory=_factory({GUILD: svc}),
        iter_guild_ids=iter_guilds,
        system_state_repo=fake_system_state_repo,
    )

    # Must NOT raise.
    with freeze_time("2026-05-25 10:00:00", tz_offset=0):
        await task._run()

    svc.reset_week_buckets.assert_awaited_once()
    state = await fake_system_state_repo.get(GUILD)
    assert state is None or state.last_weekly_reset is None


async def test_weekly_reset_isolates_service_exception_per_guild(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """A failing guild does not abort processing of the next guild.

    Per the Wave 1 #82 H8 fix, per-guild service calls are wrapped under
    ``_safe_run``; one guild's exception must not silence the rest.
    """
    svc_a = MagicMock()
    svc_a.reset_week_buckets = AsyncMock(side_effect=RuntimeError("a-boom"))
    svc_b = MagicMock()
    svc_b.reset_week_buckets = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return ["g1", "g2"]

    task = WeeklyResetTask(
        service_factory=_factory({"g1": svc_a, "g2": svc_b}),
        iter_guild_ids=iter_guilds,
        system_state_repo=fake_system_state_repo,
    )

    # Must NOT raise.
    with freeze_time("2026-05-25 10:00:00", tz_offset=0):
        await task._run()

    svc_a.reset_week_buckets.assert_awaited_once()
    svc_b.reset_week_buckets.assert_awaited_once()

    s1 = await fake_system_state_repo.get("g1")
    s2 = await fake_system_state_repo.get("g2")
    # g1 failed: weekly state not advanced.
    assert s1 is None or s1.last_weekly_reset is None
    # g2 succeeded: weekly state advanced.
    assert s2 is not None and s2.last_weekly_reset is not None


async def test_weekly_reset_does_not_refire_on_backward_clock_drift(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """W8: a backward clock jump must NOT re-trigger the weekly reset.

    Wave 1 PR #89 fix-up (L-2): the stale-check used ``!=`` to compare ISO
    ``(year, week)`` pairs, so a *backward* clock jump (clock skew, manual DB
    edit, NTP correction) into a prior ISO week would re-fire the reset
    every tick until wall-clock caught up. The fix: use ``>`` so the
    comparison is monotonic — only forward week rollovers fire.

    Concretely: seed ``last_weekly_reset`` to ISO week 23 of 2026, then
    drive a tick at a clock pointing to ISO week 22 of 2026. The reset
    MUST NOT fire.
    """
    svc = MagicMock()
    svc.reset_week_buckets = AsyncMock(return_value=None)

    # Seed: last weekly reset happened in ISO week 23 of 2026 (2026-06-01 is
    # Mon of week 23).
    future_marker = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    await fake_system_state_repo.upsert(
        SystemState(
            guild_id=GUILD,
            last_daily_reset=None,
            last_weekly_reset=future_marker,
        )
    )

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = WeeklyResetTask(
        service_factory=_factory({GUILD: svc}),
        iter_guild_ids=iter_guilds,
        system_state_repo=fake_system_state_repo,
    )

    # Tick at an EARLIER ISO week (week 22 of 2026 = 2026-05-25 Mon).
    with freeze_time("2026-05-25 12:00:00", tz_offset=0):
        await task._run()

    # Reset must NOT have re-fired — the stored marker is in the future.
    svc.reset_week_buckets.assert_not_awaited()
    state = await fake_system_state_repo.get(GUILD)
    assert state is not None
    assert state.last_weekly_reset == future_marker


async def test_weekly_reset_preserves_daily_reset_field(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """W7: a weekly reset never clobbers an existing ``last_daily_reset``."""
    daily_marker = datetime(2026, 5, 24, 6, 0, tzinfo=UTC)
    await fake_system_state_repo.upsert(
        SystemState(
            guild_id=GUILD,
            last_daily_reset=daily_marker,
            last_weekly_reset=None,
        )
    )

    svc = MagicMock()
    svc.reset_week_buckets = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = WeeklyResetTask(
        service_factory=_factory({GUILD: svc}),
        iter_guild_ids=iter_guilds,
        system_state_repo=fake_system_state_repo,
    )

    with freeze_time("2026-05-25 12:00:00", tz_offset=0):
        await task._run()

    state = await fake_system_state_repo.get(GUILD)
    assert state is not None
    assert state.last_daily_reset == daily_marker
    assert state.last_weekly_reset is not None
