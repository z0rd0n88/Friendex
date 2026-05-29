"""Behavioural tests for :class:`InactivityDecayTask` (Phase 9 AC2).

Wraps :meth:`PriceTickService.inactivity_decay_tick` on a 5-minute cadence with
per-guild fan-out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from friendex.adapters.tasks.inactivity_decay_task import InactivityDecayTask

if TYPE_CHECKING:
    from friendex.application.price_tick_service import PriceTickService


def _factory(services: dict[str, PriceTickService]) -> object:
    def factory(guild_id: str) -> PriceTickService:
        return services[guild_id]

    return factory


async def test_inactivity_decay_task_invokes_service_per_guild() -> None:
    """The task calls ``inactivity_decay_tick`` once per registered guild."""
    svc_a = MagicMock()
    svc_a.inactivity_decay_tick = AsyncMock(return_value=None)
    svc_b = MagicMock()
    svc_b.inactivity_decay_tick = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return ["g1", "g2"]

    task = InactivityDecayTask(
        service_factory=_factory({"g1": svc_a, "g2": svc_b}),
        iter_guild_ids=iter_guilds,
    )
    await task._run()

    svc_a.inactivity_decay_tick.assert_awaited_once()
    svc_b.inactivity_decay_tick.assert_awaited_once()


async def test_inactivity_decay_task_isolates_service_exception_per_guild() -> None:
    """Per-guild failure isolation: g1 raises, g2 still ticks; ``_run`` does NOT raise.

    Each per-guild call is wrapped under ``_safe_run`` so a transient service
    failure on one guild cannot abort the sweep over the others.
    """
    svc_a = MagicMock()
    svc_a.inactivity_decay_tick = AsyncMock(side_effect=RuntimeError("nope"))
    svc_b = MagicMock()
    svc_b.inactivity_decay_tick = AsyncMock(return_value=None)

    async def iter_guilds() -> list[str]:
        return ["g1", "g2"]

    task = InactivityDecayTask(
        service_factory=_factory({"g1": svc_a, "g2": svc_b}),
        iter_guild_ids=iter_guilds,
    )

    # Must NOT raise.
    await task._run()

    svc_a.inactivity_decay_tick.assert_awaited_once()
    svc_b.inactivity_decay_tick.assert_awaited_once()


def test_inactivity_decay_task_cadence_is_five_minutes() -> None:
    """The declared cadence is 5 minutes (spec-pinned)."""
    assert InactivityDecayTask.interval_minutes == 5
    assert InactivityDecayTask.interval_hours == 0
