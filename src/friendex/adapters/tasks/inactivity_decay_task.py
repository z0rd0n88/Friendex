"""5-minute background task that decays the price of inactive users.

:class:`InactivityDecayTask` wraps
:meth:`friendex.application.price_tick_service.PriceTickService.inactivity_decay_tick`
with per-guild fan-out. The 5-minute cadence is declared on
:attr:`interval_minutes`; the Phase 14 composition layer wraps :meth:`_run`
in a ``discord.ext.tasks.loop(minutes=5)`` and binds it to ``self._loop``.

The service applies a flat ``settings.inactivity_decay`` (default 4%) to every
account whose ``last_activity`` is older than
``settings.inactivity_threshold_seconds`` and floors at ``min_price`` — the
task itself owns no rule, only the scheduling and the swallow-and-log
contract from :class:`~friendex.adapters.tasks.base_task.BackgroundTask`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from friendex.adapters.tasks.base_task import BackgroundTask

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from friendex.application.price_tick_service import PriceTickService


class InactivityDecayTask(BackgroundTask):
    """5-minute sweep: ``inactivity_decay_tick`` per guild."""

    interval_minutes = 5

    def __init__(
        self,
        *,
        service_factory: Callable[[str], PriceTickService],
        iter_guild_ids: Callable[[], Awaitable[Iterable[str]]],
    ) -> None:
        self._service_factory = service_factory
        self._iter_guild_ids = iter_guild_ids

    async def _run(self) -> None:
        """Per-tick body — fan out one service call per guild."""
        for guild_id in await self._iter_guild_ids():
            service = self._service_factory(guild_id)
            await service.inactivity_decay_tick()
