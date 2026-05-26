"""1-minute polling task that fires the weekly activity-bucket reset.

:class:`WeeklyResetTask` is structurally identical to
:class:`~friendex.adapters.tasks.daily_reset_task.DailyResetTask` but keys
its boundary check on the ISO ``(year, week)`` pair so the fire-exactly-
once contract holds across ISO-week and ISO-year rollovers.

**Why ISO ``(year, week)`` and not Monday-only.** Keying on
``isocalendar().week`` alone would re-fire after a Dec-29 → Jan-1 rollover
into ISO week 1 of the next ISO year — but that's a legitimate boundary
crossing, so the desired-once-per-week behavior depends on the year
component being part of the key. Conversely, ``utcnow().weekday() == 0``
(Monday) would fire on EVERY Monday tick rather than on the first one and
no others. The pair check is the cleanest expression of "did the ISO week
roll between this tick and the last persisted reset?"

The reset-then-advance-state order mirrors the daily task: a service
failure leaves state unadvanced so the next tick retries; the upsert
preserves ``last_daily_reset``.

**Cadence is declared.** ``interval_minutes = 1``; the Phase 14 composition
layer wraps :meth:`_run` in a ``discord.ext.tasks.loop(minutes=1)``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from friendex.adapters.tasks.base_task import BackgroundTask
from friendex.application.interfaces import SystemState

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from friendex.application.activity_service import ActivityService
    from friendex.application.interfaces import ISystemStateRepo


def _iso_year_week(moment: datetime) -> tuple[int, int]:
    """Return the ``(iso_year, iso_week)`` pair that uniquely identifies a week."""
    cal = moment.isocalendar()
    return (cal.year, cal.week)


class WeeklyResetTask(BackgroundTask):
    """1-minute poll: reset week buckets when the ISO ``(year, week)`` rolls."""

    interval_minutes = 1

    def __init__(
        self,
        *,
        service_factory: Callable[[str], ActivityService],
        iter_guild_ids: Callable[[], Awaitable[Iterable[str]]],
        system_state_repo: ISystemStateRepo,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._service_factory = service_factory
        self._iter_guild_ids = iter_guild_ids
        self._state_repo = system_state_repo
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(tz=UTC))

    async def _run(self) -> None:
        """Per-tick body — act per stale guild; service-then-state ordering."""
        now = self._clock()
        for guild_id in await self._iter_guild_ids():
            if not await self._is_stale(guild_id, now):
                continue
            success = await self._try_reset(guild_id)
            if success:
                await self._advance_state(guild_id, now)

    async def _is_stale(self, guild_id: str, now: datetime) -> bool:
        """``True`` iff the persisted ISO ``(year, week)`` is older than now's."""
        state = await self._state_repo.get(guild_id)
        if state is None or state.last_weekly_reset is None:
            return True
        return _iso_year_week(now) != _iso_year_week(state.last_weekly_reset)

    async def _try_reset(self, guild_id: str) -> bool:
        """Call the service under ``_safe_run``; return ``True`` iff successful."""
        succeeded: dict[str, bool] = {}
        service = self._service_factory(guild_id)

        async def call() -> None:
            await service.reset_week_buckets()
            succeeded["ok"] = True

        await self._safe_run(call())
        return succeeded.get("ok", False)

    async def _advance_state(self, guild_id: str, now: datetime) -> None:
        """Upsert :class:`SystemState` with ``last_weekly_reset = now``.

        Preserves ``last_daily_reset`` so the two reset clocks stay
        independent.
        """
        existing = await self._state_repo.get(guild_id)
        new_state = SystemState(
            guild_id=guild_id,
            last_daily_reset=(existing.last_daily_reset if existing else None),
            last_weekly_reset=now,
        )
        await self._state_repo.upsert(new_state)
