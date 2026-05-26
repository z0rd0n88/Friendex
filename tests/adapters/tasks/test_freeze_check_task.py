"""Behavioural tests for :class:`FreezeCheckTask` (Phase 9 AC4).

The task is a 5-minute wrapper around
:meth:`TradingService.update_frozen_shorts`. It iterates every guild (via the
injected ``iter_guild_ids`` callable), builds a per-guild
:class:`TradingService` from the injected factory, and delegates the sweep.

Acceptance criteria pinned here:

* **F1** — calling the task body invokes ``update_frozen_shorts`` for every
  guild returned by ``iter_guild_ids`` (per-guild fan-out works for N=2).
* **F2** — an exception raised by the underlying service on ONE guild does
  not stop the sweep from processing other guilds (each per-guild call is
  ``_safe_run``-wrapped) AND does not propagate out of the task body.
* **F3** — the cadence is 5 minutes (the spec-pinned interval).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from friendex.adapters.tasks.freeze_check_task import FreezeCheckTask

if TYPE_CHECKING:
    from friendex.application.trading_service import TradingService


def _make_factory(services: dict[str, TradingService]) -> object:
    """Build a ``service_factory`` callable that returns the registered double."""

    def factory(guild_id: str) -> TradingService:
        return services[guild_id]

    return factory


async def test_freeze_check_task_invokes_service_per_guild() -> None:
    """F1: ``update_frozen_shorts`` is called once per guild returned by iter."""
    svc_a = MagicMock()
    svc_a.update_frozen_shorts = AsyncMock(return_value=None)
    svc_b = MagicMock()
    svc_b.update_frozen_shorts = AsyncMock(return_value=None)

    factory = _make_factory({"g1": svc_a, "g2": svc_b})

    async def iter_guilds() -> list[str]:
        return ["g1", "g2"]

    task = FreezeCheckTask(service_factory=factory, iter_guild_ids=iter_guilds)
    await task._run()

    svc_a.update_frozen_shorts.assert_awaited_once()
    svc_b.update_frozen_shorts.assert_awaited_once()


async def test_freeze_check_task_swallows_service_exception() -> None:
    """F2: a per-guild service exception does not propagate or stop the sweep."""
    svc_a = MagicMock()
    svc_a.update_frozen_shorts = AsyncMock(side_effect=RuntimeError("kaboom"))
    svc_b = MagicMock()
    svc_b.update_frozen_shorts = AsyncMock(return_value=None)

    factory = _make_factory({"g1": svc_a, "g2": svc_b})

    async def iter_guilds() -> list[str]:
        return ["g1", "g2"]

    task = FreezeCheckTask(service_factory=factory, iter_guild_ids=iter_guilds)
    # Must NOT raise.
    await task._run()

    svc_a.update_frozen_shorts.assert_awaited_once()
    # The second guild is still processed.
    svc_b.update_frozen_shorts.assert_awaited_once()


def test_freeze_check_task_cadence_is_five_minutes() -> None:
    """F3: the declared cadence matches the 5-minute spec.

    The composition layer (Phase 14) reads :attr:`interval_minutes` and wraps
    :meth:`_run` in a ``discord.ext.tasks.loop(minutes=5)`` — keeping the
    cadence as a class attribute means this test does not need to import
    ``discord``.
    """
    assert FreezeCheckTask.interval_minutes == 5
    assert FreezeCheckTask.interval_hours == 0
