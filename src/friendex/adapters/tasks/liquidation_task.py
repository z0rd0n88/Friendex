"""5-minute background task that auto-covers liquidation-threshold shorts.

:class:`LiquidationTask` wraps
:meth:`friendex.application.liquidation_service.LiquidationService.check_and_liquidate_shorts`
with per-guild fan-out, and forwards every emitted
:class:`~friendex.application.liquidation_events.LiquidationEvent` to a
generic notifier callback injected at construction.

**Why the notifier is injected (Phase 9 AC3 / Phase 8f digest).** The task
must not import the ``discord`` package — the Discord embed/channel plumbing
lives in the Phase 14 composition layer, which provides the notifier as a
plain ``Callable[[LiquidationEvent], Awaitable[None]]``. This keeps the
hexagonal arrow pointed inward: a future test, CLI driver, or alternative
chat backend can substitute its own notifier without touching the task.

**Notifier failure isolation.** Each notifier invocation is wrapped in
:meth:`BackgroundTask._safe_run` independently so a malformed embed on one
event does not block the rest of the per-tick stream. Likewise, a service
failure on one guild does not abort the next guild's sweep.

**Cadence is declared, not scheduled here.** ``interval_minutes = 5`` is
read by the Phase 14 composition layer, which wraps :meth:`_run` in a
``discord.ext.tasks.loop(minutes=5)`` and binds ``self._loop``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from friendex.adapters.tasks.base_task import BackgroundTask

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable
    from datetime import datetime

    from friendex.application.liquidation_events import LiquidationEvent
    from friendex.application.liquidation_service import LiquidationService


class LiquidationTask(BackgroundTask):
    """5-minute sweep: ``check_and_liquidate_shorts`` per guild + notifier fan-out."""

    interval_minutes = 5

    def __init__(
        self,
        *,
        service_factory: Callable[[str], LiquidationService],
        iter_guild_ids: Callable[[], Awaitable[Iterable[str]]],
        notifier: Callable[[LiquidationEvent], Awaitable[None]],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        from datetime import UTC
        from datetime import datetime as _datetime

        self._service_factory = service_factory
        self._iter_guild_ids = iter_guild_ids
        self._notifier = notifier
        # The service requires a `now` per call so the emitted event timestamps
        # are deterministic in tests. Default to wall-clock UTC.
        self._clock: Callable[[], datetime] = clock or (lambda: _datetime.now(tz=UTC))

    def bind_notifier(
        self, notifier: Callable[[LiquidationEvent], Awaitable[None]]
    ) -> None:
        """Public seam for installing the live notifier callback.

        Wave 1 (#82 H15 / #84 H): replaces direct ``task._notifier = fn``
        mutation from the container. The notifier is intentionally a
        plain ``Callable[[LiquidationEvent], Awaitable[None]]`` so the
        task itself stays free of any ``discord`` import (Phase 9 AC3).
        """
        self._notifier = notifier

    async def _run(self) -> None:
        """Per-tick body — sweep each guild, then notify each emitted event.

        Each notifier invocation is wrapped in
        :meth:`BackgroundTask._safe_run` so one bad embed / permission error
        does not abort the rest of the per-tick notification stream
        (Wave 1 #82 H5).
        """
        for guild_id in await self._iter_guild_ids():
            service = self._service_factory(guild_id)
            events = await self._collect_events(service)
            for event in events:
                await self._safe_run(self._notifier(event))

    async def _collect_events(
        self, service: LiquidationService
    ) -> list[LiquidationEvent]:
        """Run the service sweep and return its events; on failure, return ``[]``.

        Failure path: a raised exception inside ``service.check_and_liquidate_shorts``
        is swallowed by :meth:`BackgroundTask._safe_run` (logged with traceback
        via the base class), and the helper returns the empty list it was
        already accumulating into — so callers should expect ``[]`` on a
        service exception and not a propagated raise.
        """
        events: list[LiquidationEvent] = []
        now = self._clock()

        async def run() -> None:
            collected = await service.check_and_liquidate_shorts(now)
            events.extend(collected)

        await self._safe_run(run())
        return events
