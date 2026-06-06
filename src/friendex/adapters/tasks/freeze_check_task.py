"""5-minute background task that flips frozen-flag on aged short positions.

:class:`FreezeCheckTask` wraps
:meth:`friendex.application.trading_service.TradingService.update_frozen_shorts`
with per-guild fan-out. The 5-minute cadence is declared on
:attr:`interval_minutes`; the Phase 14 composition layer wraps :meth:`_run`
in a ``discord.ext.tasks.loop(minutes=5)`` and binds it to ``self._loop``.

**Per-guild design (ADR-0001 + Phase 8a digest).** Services are per-guild —
each :class:`TradingService` instance captures one ``guild_id`` at
construction. The task is single-instance and takes a ``service_factory``
callable that produces a service for a given ``guild_id``, plus an
``iter_guild_ids`` async callable that yields the set of guilds the bot is in.
Each tick walks that set and calls the per-guild service through
:meth:`BackgroundTask._safe_run`, so a transient failure on one guild cannot
abort the sweep over the others.

**No domain or persistence concerns here.** The task is a thin scheduler; the
freeze rules live in the application service.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from friendex.adapters.tasks.base_task import BackgroundTask

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from friendex.application.trading_service import TradingService


class FreezeCheckTask(BackgroundTask):
    """5-minute sweep that freezes shorts older than ``short_freeze_minutes``."""

    interval_minutes = 5

    def __init__(
        self,
        *,
        service_factory: Callable[[str], TradingService],
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
            lambda gid: self._service_factory(gid).update_frozen_shorts()
        )
