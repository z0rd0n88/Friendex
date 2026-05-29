"""Behavioural tests for :class:`DailyResetTask` (Phase 9 AC6).

The task runs every minute but only **acts** when the UTC date has rolled past
the persisted ``SystemState.last_daily_reset``. When it acts:

1. ``ActivityService.reset_today_buckets`` is called for the guild;
2. ``SystemStateRepository.upsert`` writes a new ``last_daily_reset = now``.

If ``last_daily_reset is None`` (a guild with no state row yet) the task
acts on the very first tick and seeds the row.

Acceptance criteria:

* **D1** — first tick on a fresh guild: service is called and state is seeded.
* **D2** — subsequent tick within the same UTC date: service is NOT called.
* **D3** — tick after the UTC date has rolled: service IS called again exactly
  once, state advances. Three ticks across a midnight boundary fire exactly
  ONCE.
* **D4** — declared cadence is 1 minute.
* **D5** — a service exception does not block state update? NO — the spec
  says state advances only when the service call succeeds (otherwise we'd
  silently miss a day's reset on a transient failure). We TEST that on
  service failure, state does NOT advance, so the next tick retries.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from freezegun import freeze_time

from friendex.adapters.tasks.daily_reset_task import DailyResetTask
from friendex.application.interfaces import SystemState

if TYPE_CHECKING:
    from friendex.application.activity_service import ActivityService
    from tests.application.fakes.fake_repos import FakeSystemStateRepo


GUILD = "g1"


def _factory(services: dict[str, ActivityService]) -> object:
    def factory(guild_id: str) -> ActivityService:
        return services[guild_id]

    return factory


async def test_daily_reset_first_tick_on_fresh_guild(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """D1: a guild with no state row resets on the first tick and seeds state."""
    svc = MagicMock()
    svc.reset_today_buckets = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = DailyResetTask(
        service_factory=_factory({GUILD: svc}),
        iter_guild_ids=iter_guilds,
        system_state_repo=fake_system_state_repo,
    )

    with freeze_time("2026-05-25 10:30:00", tz_offset=0):
        await task._run()

    svc.reset_today_buckets.assert_awaited_once()
    state = await fake_system_state_repo.get(GUILD)
    assert state is not None
    assert state.last_daily_reset is not None
    assert state.last_daily_reset.date() == datetime(2026, 5, 25, tzinfo=UTC).date()


async def test_daily_reset_no_op_within_same_utc_date(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """D2: a second tick within the same UTC date does NOT call the service."""
    svc = MagicMock()
    svc.reset_today_buckets = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = DailyResetTask(
        service_factory=_factory({GUILD: svc}),
        iter_guild_ids=iter_guilds,
        system_state_repo=fake_system_state_repo,
    )

    with freeze_time("2026-05-25 00:01:00", tz_offset=0):
        await task._run()
    with freeze_time("2026-05-25 23:59:00", tz_offset=0):
        await task._run()

    svc.reset_today_buckets.assert_awaited_once()  # ONLY the first tick


async def test_daily_reset_fires_exactly_once_across_midnight(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """D3: ticks straddling a midnight boundary fire the service exactly twice.

    First tick on 2026-05-25 seeds state; ticks within that date no-op; first
    tick on 2026-05-26 fires again exactly once.
    """
    svc = MagicMock()
    svc.reset_today_buckets = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = DailyResetTask(
        service_factory=_factory({GUILD: svc}),
        iter_guild_ids=iter_guilds,
        system_state_repo=fake_system_state_repo,
    )

    # Within day 1 — first tick acts, second no-ops.
    with freeze_time("2026-05-25 10:00:00", tz_offset=0):
        await task._run()
    with freeze_time("2026-05-25 23:59:00", tz_offset=0):
        await task._run()
    # Cross midnight UTC — first tick of day 2 acts again.
    with freeze_time("2026-05-26 00:01:00", tz_offset=0):
        await task._run()
    # Subsequent ticks within day 2 — no-op.
    with freeze_time("2026-05-26 12:00:00", tz_offset=0):
        await task._run()

    assert svc.reset_today_buckets.await_count == 2


def test_daily_reset_cadence_is_one_minute() -> None:
    """D4: the declared cadence is 1 minute."""
    assert DailyResetTask.interval_minutes == 1
    assert DailyResetTask.interval_hours == 0


async def test_daily_reset_state_not_advanced_on_service_failure(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """D5: a failing service call does NOT advance state — next tick retries.

    Each per-guild call is wrapped under ``_safe_run`` so the exception is
    isolated (does NOT propagate). Because ``_advance_state`` is only called
    after a successful reset, the failure path leaves the state unchanged
    and the next tick retries.
    """
    svc = MagicMock()
    svc.reset_today_buckets = AsyncMock(side_effect=RuntimeError("oops"))

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = DailyResetTask(
        service_factory=_factory({GUILD: svc}),
        iter_guild_ids=iter_guilds,
        system_state_repo=fake_system_state_repo,
    )

    # Must NOT raise.
    with freeze_time("2026-05-25 10:00:00", tz_offset=0):
        await task._run()

    svc.reset_today_buckets.assert_awaited_once()
    state = await fake_system_state_repo.get(GUILD)
    # No upsert on failure path — state is None (or its last_daily_reset is None).
    assert state is None or state.last_daily_reset is None


async def test_daily_reset_isolates_service_exception_per_guild(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """A failing guild does not abort processing of the next guild.

    Per the Wave 1 #82 H8 fix, per-guild service calls are wrapped under
    ``_safe_run``; one guild's exception must not silence the rest. The
    successful guild still advances its state; the failed guild does not.
    """
    svc_a = MagicMock()
    svc_a.reset_today_buckets = AsyncMock(side_effect=RuntimeError("a-boom"))
    svc_b = MagicMock()
    svc_b.reset_today_buckets = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return ["g1", "g2"]

    task = DailyResetTask(
        service_factory=_factory({"g1": svc_a, "g2": svc_b}),
        iter_guild_ids=iter_guilds,
        system_state_repo=fake_system_state_repo,
    )

    # Must NOT raise.
    with freeze_time("2026-05-25 10:00:00", tz_offset=0):
        await task._run()

    svc_a.reset_today_buckets.assert_awaited_once()
    svc_b.reset_today_buckets.assert_awaited_once()

    s1 = await fake_system_state_repo.get("g1")
    s2 = await fake_system_state_repo.get("g2")
    # g1 failed: state not advanced.
    assert s1 is None or s1.last_daily_reset is None
    # g2 succeeded: state advanced.
    assert s2 is not None and s2.last_daily_reset is not None


async def test_daily_reset_isolates_state_repo_exception_per_guild(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """A state-repo IO failure on guild A does not abort guild B's processing.

    Per the Wave 1 #84 H audit, the per-guild block — including the
    stale-check read against the state repo — is wrapped under ``_safe_run``
    so a transient repo failure on one guild cannot silence the rest of the
    sweep. Guild B still ticks against the working state repo.
    """
    from unittest.mock import patch

    svc_a = MagicMock()
    svc_a.reset_today_buckets = AsyncMock(return_value=None)
    svc_b = MagicMock()
    svc_b.reset_today_buckets = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return ["g1", "g2"]

    task = DailyResetTask(
        service_factory=_factory({"g1": svc_a, "g2": svc_b}),
        iter_guild_ids=iter_guilds,
        system_state_repo=fake_system_state_repo,
    )

    original_get = fake_system_state_repo.get
    call_count = {"n": 0}

    async def flaky_get(guild_id: str) -> object:
        call_count["n"] += 1
        if guild_id == "g1":
            raise RuntimeError("g1 state repo down")
        return await original_get(guild_id)

    with (
        freeze_time("2026-05-25 10:00:00", tz_offset=0),
        patch.object(fake_system_state_repo, "get", side_effect=flaky_get),
    ):
        await task._run()  # Must NOT raise.

    # g1 service NOT called (stale-check raised first); g2 service called.
    svc_a.reset_today_buckets.assert_not_awaited()
    svc_b.reset_today_buckets.assert_awaited_once()


async def test_daily_reset_only_fires_for_stale_guilds(
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    """A guild whose last_daily_reset is already today is silently skipped."""
    svc = MagicMock()
    svc.reset_today_buckets = AsyncMock(return_value=None)

    # Pre-seed state with today's date so the task should NOT fire.
    await fake_system_state_repo.upsert(
        SystemState(
            guild_id=GUILD,
            last_daily_reset=datetime(2026, 5, 25, 6, 0, tzinfo=UTC),
        )
    )

    async def iter_guilds() -> list[str]:
        return [GUILD]

    task = DailyResetTask(
        service_factory=_factory({GUILD: svc}),
        iter_guild_ids=iter_guilds,
        system_state_repo=fake_system_state_repo,
    )

    with freeze_time("2026-05-25 10:00:00", tz_offset=0):
        await task._run()

    svc.reset_today_buckets.assert_not_awaited()
