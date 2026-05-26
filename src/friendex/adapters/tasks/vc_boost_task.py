"""15-minute background task that drives the VC extra-responder boost loop.

:class:`VcBoostTask` wraps
:meth:`friendex.application.price_tick_service.PriceTickService.vc_boost_tick`
with per-guild fan-out and **owns the volatile
:class:`~friendex.domain.models.VcExtraBoost` storage**. Per the Phase 8b
digest §5 storage-by-parameter convention, the service is stateless: each
tick passes the current per-guild snapshot in and replaces the in-memory
store with the survivors the service returns.

**Why the task owns storage.** The original bot held its
``vc_extra_boosts`` map in process memory and rebuilt it every loop. The
hexagonal rebuild splits the rule (in the price-tick service) from the
storage (here) — a future enhancement (per the Phase 8b deferred list)
could swap this for a persistence-backed ``VcExtraBoostStore`` without
touching the service signature.

**Per-guild isolation.** Each guild keeps its own list under
``self._stores[guild_id]``; a service exception on one guild preserves
that guild's previous store unchanged (the survivor swap only happens on
a successful tick).

**Cadence is declared.** ``interval_minutes = 15`` is read by the Phase 14
composition layer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from friendex.adapters.tasks.base_task import BackgroundTask

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from friendex.application.price_tick_service import PriceTickService
    from friendex.domain.models import VcExtraBoost


class VcBoostTask(BackgroundTask):
    """15-minute sweep: per-guild ``vc_boost_tick`` with task-owned storage."""

    interval_minutes = 15

    def __init__(
        self,
        *,
        service_factory: Callable[[str], PriceTickService],
        iter_guild_ids: Callable[[], Awaitable[Iterable[str]]],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._service_factory = service_factory
        self._iter_guild_ids = iter_guild_ids
        self._stores: dict[str, list[VcExtraBoost]] = {}
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(tz=UTC))

    def set_store_for_guild(self, guild_id: str, boosts: list[VcExtraBoost]) -> None:
        """Seed (or replace) the per-guild boost store.

        Composition wires this up to whatever produces extra-boost entries
        (the Phase 12 voice-ping listener); tests use it to pre-populate
        the store before exercising :meth:`_run`.
        """
        # Copy the input to avoid letting the caller mutate our store.
        self._stores[guild_id] = list(boosts)

    def get_store_for_guild(self, guild_id: str) -> list[VcExtraBoost]:
        """Return a copy of the per-guild store (empty if uninitialized)."""
        return list(self._stores.get(guild_id, []))

    async def _run(self) -> None:
        """Per-tick body — sweep each guild, threading survivors back into store.

        Service exceptions are isolated via :meth:`_safe_run`; on failure the
        per-guild store is preserved (no partial swap).
        """
        now = self._clock()
        for guild_id in await self._iter_guild_ids():
            service = self._service_factory(guild_id)
            current = self.get_store_for_guild(guild_id)
            survivors = await self._invoke(service, current, now)
            if survivors is not None:
                self._stores[guild_id] = survivors

    async def _invoke(
        self,
        service: PriceTickService,
        current: list[VcExtraBoost],
        now: datetime,
    ) -> list[VcExtraBoost] | None:
        """Call ``vc_boost_tick`` under ``_safe_run``; return survivors or None.

        A ``None`` result signals the service raised (the caller leaves the
        per-guild store untouched). On success the returned list is the
        survivor set the caller swaps in.
        """
        captured: dict[str, list[VcExtraBoost]] = {}

        async def call() -> None:
            captured["survivors"] = await service.vc_boost_tick(
                extra_boosts=current, now=now
            )

        await self._safe_run(call())
        return captured.get("survivors")
