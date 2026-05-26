"""Behavioural tests for :class:`ActivityTickTask` (Phase 9 AC1).

Wraps :meth:`PriceTickService.activity_price_tick` on a 15-minute cadence with
per-guild fan-out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from friendex.adapters.tasks.activity_tick_task import ActivityTickTask

if TYPE_CHECKING:
    from friendex.application.price_tick_service import PriceTickService


def _factory(services: dict[str, PriceTickService]) -> object:
    def factory(guild_id: str) -> PriceTickService:
        return services[guild_id]

    return factory


async def test_activity_tick_task_invokes_service_per_guild() -> None:
    """The task calls ``activity_price_tick`` once per registered guild."""
    svc_a = MagicMock()
    svc_a.activity_price_tick = AsyncMock(return_value=None)
    svc_b = MagicMock()
    svc_b.activity_price_tick = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return ["g1", "g2"]

    task = ActivityTickTask(
        service_factory=_factory({"g1": svc_a, "g2": svc_b}),
        iter_guild_ids=iter_guilds,
    )
    await task._run()

    svc_a.activity_price_tick.assert_awaited_once()
    svc_b.activity_price_tick.assert_awaited_once()


async def test_activity_tick_task_swallows_service_exception() -> None:
    """A per-guild failure does not abort the rest of the sweep."""
    svc_a = MagicMock()
    svc_a.activity_price_tick = AsyncMock(side_effect=ValueError("nope"))
    svc_b = MagicMock()
    svc_b.activity_price_tick = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return ["g1", "g2"]

    task = ActivityTickTask(
        service_factory=_factory({"g1": svc_a, "g2": svc_b}),
        iter_guild_ids=iter_guilds,
    )
    await task._run()

    svc_b.activity_price_tick.assert_awaited_once()


def test_activity_tick_task_cadence_is_fifteen_minutes() -> None:
    """The declared cadence is 15 minutes (spec-pinned).

    Cadence is a class attribute so the Phase 14 composition layer can read
    it without forcing this module to import ``discord``.
    """
    assert ActivityTickTask.interval_minutes == 15
    assert ActivityTickTask.interval_hours == 0
