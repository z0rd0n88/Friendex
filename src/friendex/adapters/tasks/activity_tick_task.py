"""15-minute background task that ticks every user's price from activity.

:class:`ActivityTickTask` wraps
:meth:`friendex.application.price_tick_service.PriceTickService.activity_price_tick`
with per-guild fan-out. The 15-minute cadence is declared on
:attr:`interval_minutes`; the Phase 14 composition layer wraps :meth:`_run` in
a ``discord.ext.tasks.loop(minutes=15)`` and binds it to ``self._loop``.

See :class:`~friendex.adapters.tasks.base_task.BackgroundTask` for the
swallow-and-log error contract and the cadence-as-declaration design.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from friendex.adapters.tasks.base_task import BackgroundTask

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from friendex.application.price_tick_service import PriceTickService


class ActivityTickTask(BackgroundTask):
    """15-minute sweep: ``activity_price_tick`` per guild."""

    interval_minutes = 15

    def __init__(
        self,
        *,
        service_factory: Callable[[str], PriceTickService],
        iter_guild_ids: Callable[[], Awaitable[Iterable[str]]],
    ) -> None:
        self._service_factory = service_factory
        self._iter_guild_ids = iter_guild_ids

    async def _run(self) -> None:
        """Per-tick body — fan out one service call per guild.

        Uses :meth:`BackgroundTask.for_each_guild` so per-guild isolation is
        enforced by the base class: a transient exception on one guild never
        aborts the sweep over the others (Wave 1 #82 H6 / #84 H).
        """
        await self.for_each_guild(
            lambda gid: self._service_factory(gid).activity_price_tick()
        )
