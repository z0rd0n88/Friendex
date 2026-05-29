"""1-minute polling task that fires the daily activity-bucket reset.

:class:`DailyResetTask` polls every minute and acts only when the **current
UTC date** is strictly later than the persisted
:attr:`~friendex.application.interfaces.SystemState.last_daily_reset`'s date.
On a fresh guild (no state row, or ``last_daily_reset is None``) the very
first tick acts and seeds the row.

When the task acts on a guild, it:

1. Calls :meth:`ActivityService.reset_today_buckets`.
2. Upserts the guild's :class:`SystemState` with
   ``last_daily_reset = datetime.now(tz=UTC)``.

The upsert happens AFTER the service call so a service failure leaves
``last_daily_reset`` unchanged and the next tick retries — otherwise a
transient failure would silently skip a day's reset. The service-call path
itself is wrapped in :meth:`BackgroundTask._safe_run` so the loop never
sees a propagated exception.

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


class DailyResetTask(BackgroundTask):
    """1-minute poll: reset today buckets when the UTC date rolls."""

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
        """Per-tick body — act per stale guild; service-then-state ordering.

        Each guild's full processing block (stale-check + reset + state
        advance) is wrapped in :meth:`BackgroundTask._safe_run` so a
        per-guild exception in ANY phase — service call OR state repo IO —
        does not silence the rest of the sweep (Wave 1 #82 H8 / #84 H). The
        state is advanced ONLY on a successful service call, so failed
        guilds retry on the next tick (preserves the service-then-state
        ordering invariant).
        """
        now = self._clock()
        for guild_id in await self._iter_guild_ids():
            await self._safe_run(self._process_guild(guild_id, now))

    async def _process_guild(self, guild_id: str, now: datetime) -> None:
        """Process one guild's daily reset: stale-check, reset, advance state."""
        if not await self._is_stale(guild_id, now):
            return
        service = self._service_factory(guild_id)
        await service.reset_today_buckets()
        await self._advance_state(guild_id, now)

    async def _is_stale(self, guild_id: str, now: datetime) -> bool:
        """Return ``True`` iff the guild's last reset is older than today (UTC)."""
        state = await self._state_repo.get(guild_id)
        if state is None or state.last_daily_reset is None:
            return True
        return now.date() > state.last_daily_reset.date()

    async def _advance_state(self, guild_id: str, now: datetime) -> None:
        """Upsert :class:`SystemState` with ``last_daily_reset = now``.

        Preserves ``last_weekly_reset``, ``last_monthly_rollover`` and
        ``last_portfolio_capture`` if they were already set
        (read-modify-write on the existing row).
        """
        existing = await self._state_repo.get(guild_id)
        new_state = SystemState(
            guild_id=guild_id,
            last_daily_reset=now,
            last_weekly_reset=(existing.last_weekly_reset if existing else None),
            last_monthly_rollover=(
                existing.last_monthly_rollover if existing else None
            ),
            last_portfolio_capture=(
                existing.last_portfolio_capture if existing else None
            ),
        )
        await self._state_repo.upsert(new_state)
