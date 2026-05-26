"""Behavioural tests for :class:`VcBoostTask` (Phase 9 AC5).

The task is a 15-minute wrapper around
:meth:`PriceTickService.vc_boost_tick`. Per the Phase 8b digest §5
storage-by-parameter convention, the per-user
:class:`~friendex.domain.models.VcExtraBoost` list is volatile state OWNED by
the task — every tick passes the current snapshot in to the service, receives
the survivor list back, and replaces its in-memory store with the survivors.

Acceptance criteria:

* **V1** — survivors returned by tick N feed tick N+1 (storage-by-parameter
  threading is correct).
* **V2** — separate stores per guild are independent (different guilds do
  not cross-feed).
* **V3** — a service exception on one guild does not corrupt the per-guild
  store and does not abort the next guild's sweep.
* **V4** — the declared cadence is 15 minutes.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from friendex.adapters.tasks.vc_boost_task import VcBoostTask
from friendex.domain.models import VcExtraBoost

if TYPE_CHECKING:
    from friendex.application.price_tick_service import PriceTickService


NOW = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)


def _boost(user_id: str, *, last_boost: datetime = NOW) -> VcExtraBoost:
    return VcExtraBoost(
        user_id=user_id,
        ping_time=NOW - timedelta(minutes=5),
        last_boost=last_boost,
        end_time=NOW + timedelta(hours=1),
    )


def _factory(services: dict[str, PriceTickService]) -> object:
    def factory(guild_id: str) -> PriceTickService:
        return services[guild_id]

    return factory


async def test_vc_boost_task_threads_survivors_tick_to_tick() -> None:
    """V1: survivors from tick N are the input to tick N+1."""
    initial = _boost("u1")
    refreshed = replace(initial, last_boost=NOW + timedelta(minutes=15))

    captured_inputs: list[list[VcExtraBoost]] = []

    async def fake_tick(
        *, extra_boosts: list[VcExtraBoost], now: datetime
    ) -> list[VcExtraBoost]:
        snapshot = list(extra_boosts)
        captured_inputs.append(snapshot)
        # First call returns refreshed; second call should see refreshed as input.
        if not snapshot:
            return []
        if snapshot[0].last_boost == initial.last_boost:
            return [refreshed]
        return [refreshed]

    svc = MagicMock()
    svc.vc_boost_tick = AsyncMock(side_effect=fake_tick)

    async def iter_guilds() -> list[str]:
        return ["g1"]

    task = VcBoostTask(
        service_factory=_factory({"g1": svc}),
        iter_guild_ids=iter_guilds,
    )
    # Seed the task's per-guild store so the first call has work to do.
    task.set_store_for_guild("g1", [initial])

    await task._run()
    await task._run()

    assert len(captured_inputs) == 2
    # Tick-1 received the seeded initial; tick-2 received the survivor from tick-1.
    assert captured_inputs[0] == [initial]
    assert captured_inputs[1] == [refreshed]


async def test_vc_boost_task_per_guild_stores_independent() -> None:
    """V2: each guild has its own store; surviving lists don't cross-feed."""
    b1 = _boost("u1")
    b2 = _boost("u2")

    async def fake_tick_a(
        *, extra_boosts: list[VcExtraBoost], now: datetime
    ) -> list[VcExtraBoost]:
        # Drop all entries to prove cross-feed would corrupt g2.
        return []

    async def fake_tick_b(
        *, extra_boosts: list[VcExtraBoost], now: datetime
    ) -> list[VcExtraBoost]:
        return list(extra_boosts)  # passthrough

    svc_a = MagicMock()
    svc_a.vc_boost_tick = AsyncMock(side_effect=fake_tick_a)
    svc_b = MagicMock()
    svc_b.vc_boost_tick = AsyncMock(side_effect=fake_tick_b)

    async def iter_guilds() -> list[str]:
        return ["g1", "g2"]

    task = VcBoostTask(
        service_factory=_factory({"g1": svc_a, "g2": svc_b}),
        iter_guild_ids=iter_guilds,
    )
    task.set_store_for_guild("g1", [b1])
    task.set_store_for_guild("g2", [b2])

    await task._run()

    # g1's drop did NOT affect g2's passthrough.
    assert task.get_store_for_guild("g1") == []
    assert task.get_store_for_guild("g2") == [b2]


async def test_vc_boost_task_swallows_service_exception() -> None:
    """V3: a per-guild service exception does not abort the next guild's sweep."""
    svc_a = MagicMock()
    svc_a.vc_boost_tick = AsyncMock(side_effect=RuntimeError("nope"))
    svc_b = MagicMock()
    svc_b.vc_boost_tick = AsyncMock(return_value=[])

    async def iter_guilds() -> list[str]:
        return ["g1", "g2"]

    task = VcBoostTask(
        service_factory=_factory({"g1": svc_a, "g2": svc_b}),
        iter_guild_ids=iter_guilds,
    )
    task.set_store_for_guild("g1", [_boost("u1")])
    task.set_store_for_guild("g2", [_boost("u2")])

    # Must NOT raise.
    await task._run()

    # g1 raised → its store is preserved (not corrupted by the partial run).
    assert task.get_store_for_guild("g1") == [_boost("u1")]
    # g2 was still processed → store updated to the (empty) survivor list.
    assert task.get_store_for_guild("g2") == []


def test_vc_boost_task_cadence_is_fifteen_minutes() -> None:
    """V4: declared cadence is 15 minutes."""
    assert VcBoostTask.interval_minutes == 15
    assert VcBoostTask.interval_hours == 0
