"""Application service that orchestrates periodic price ticks.

:class:`PriceTickService` is the use-case layer the Phase 9 background-task
loops call to recompute stock prices on a schedule. It is a *pure orchestrator*
over the domain :mod:`friendex.domain.price_engine` and the persistence ports —
it owns no math of its own. Three methods, one per loop:

* :meth:`activity_price_tick` — every account's *today* activity bucket is
  collapsed to a price delta via
  :func:`~friendex.domain.price_engine.compute_activity_return`; the proposed
  price is ``current * (1 + delta / 100)`` (matching the original bot's
  ``activity_price_step``), clamped via
  :func:`~friendex.domain.price_engine.apply_floor_stall`.
* :meth:`inactivity_decay_tick` — for every account whose
  ``last_activity`` is older than ``settings.inactivity_threshold_seconds``,
  the stock price is decayed by ``settings.inactivity_decay`` via
  :func:`~friendex.domain.price_engine.apply_inactivity_decay` (which already
  enforces the ``min_price`` floor).
* :meth:`vc_boost_tick` — applies the periodic extra-responder boost
  (multiplier from ``settings.vc_extra_boost_multiplier``) to every user
  whose :class:`VcExtraBoost` entry is still inside its window AND who is
  still in voice (per the injected :class:`VoiceSessionStore`). Returns the
  *surviving* entries (expired ones dropped, in-voice ones with refreshed
  ``last_boost``) so the caller can persist them — the per-user state is
  volatile, exactly like the original bot's ``vc_extra_boosts`` dict, and
  storage ownership stays with the task layer (Phase 9).

**Per Phase-3a correction 4**, the ``reset_24h_high_low()`` method present in
older drafts of this service is omitted: ``high_24h`` / ``low_24h`` are
computed dynamically from price history (open-Q9 decision), not stored.

**Guild scoping (ADR-0001).** ``guild_id`` is a constructor argument so the
single shared :class:`LockManager` Phase 14 injects across every per-guild
scope cannot serialise unrelated guilds against each other. The composite
``"<guild_id>:<user_id>"`` lock key is built by :meth:`_lock_key` and used at
every mutation site.

**Concurrency.** Each per-user price write happens inside
``async with lock_manager.locked(self._lock_key(user_id))`` so concurrent
trades / activity-recording / tick paths never interleave on the same user
within the same guild. Locks are non-reentrant — at most one ``locked()``
call per critical section. The manager is process-local and injected, never
constructed per call.

**Immutability.** Stored aggregates are dataclasses but treated as immutable:
a tick mutation reads the stock, builds a replaced copy via
:func:`dataclasses.replace`, and round-trips it through ``upsert``. Inputs
are never mutated in place.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

from friendex.domain.price_engine import apply_floor_stall, apply_inactivity_decay

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from friendex.adapters.config import Settings
    from friendex.application.interfaces import IPriceRepo, IUserRepo
    from friendex.application.lock_manager import LockManager
    from friendex.application.voice_session_store import VoiceSessionStore
    from friendex.domain.models import Stock, VcExtraBoost


# Activity returns in the original bot are expressed as percentage points;
# the proposed price is ``current * (1 + return / 100)``. Kept as a module
# constant rather than a magic literal in the call site.
_PERCENT = Decimal("100")
_ONE = Decimal("1")


class PriceTickService:
    """Periodic price-tick use cases: activity, inactivity-decay, VC boost."""

    def __init__(
        self,
        *,
        guild_id: str,
        user_repo: IUserRepo,
        price_repo: IPriceRepo,
        lock_manager: LockManager,
        settings: Settings,
        voice_sessions: VoiceSessionStore,
    ) -> None:
        self._guild_id = guild_id
        self._user_repo = user_repo
        self._price_repo = price_repo
        self._locks = lock_manager
        self._settings = settings
        self._voice_sessions = voice_sessions

    # -- internal helpers ---------------------------------------------------

    def _lock_key(self, user_id: str) -> str:
        """Return the composite ``"<guild>:<user>"`` lock key (ADR-0001).

        The single :class:`LockManager` Phase 14 wires across every per-guild
        service scope would otherwise serialise the same user across guilds —
        composing the guild id into the key guarantees per-guild isolation,
        verified by the load-bearing two-guild barrier test in Phase 8a.
        """
        return f"{self._guild_id}:{user_id}"

    async def _write_price(self, stock: Stock, new_price: Decimal) -> None:
        """Persist a price change under the user's lock.

        The aggregate is replaced wholesale via :func:`dataclasses.replace`;
        nothing in the stored object is mutated in place. Caller holds the
        invariant that ``new_price`` is already quantised + floor-clamped by
        the domain layer.
        """
        async with self._locks.locked(self._lock_key(stock.user_id)):
            current = await self._price_repo.get(self._guild_id, stock.user_id)
            if current is None:
                return
            await self._price_repo.upsert(
                self._guild_id, replace(current, current=new_price)
            )

    # -- activity tick ------------------------------------------------------

    async def activity_price_tick(self) -> None:
        """Recompute every user's price from their *today* activity bucket.

        Mirrors the original ``activity_price_step``: ``proposed = current *
        (1 + return / 100)`` where ``return`` comes from
        :func:`compute_activity_return` over the user's *today* bucket. The
        result passes through :func:`apply_floor_stall` so a near-floor stock
        sinks slowly and never below ``min_price``. Users without a stock row
        are silently skipped (same as the original ``ensure_price`` no-op).
        """
        # Local imports keep the TYPE_CHECKING block honest and avoid a
        # heavy module-level dependency on the domain layer at import time.
        from friendex.domain.price_engine import compute_activity_return

        min_price = Decimal(str(self._settings.min_price))
        activity_k = self._settings.activity_tick_k

        for account in await self._user_repo.list_all(self._guild_id):
            stock = await self._price_repo.get(self._guild_id, account.user_id)
            if stock is None:
                continue

            ret_pct = compute_activity_return(account.today, activity_k)
            proposed = stock.current * (_ONE + ret_pct / _PERCENT)
            new_price = apply_floor_stall(stock.current, proposed, min_price)
            if new_price == stock.current:
                continue  # no-op — skip the lock + write entirely
            await self._write_price(stock, new_price)

    # -- inactivity decay tick ---------------------------------------------

    async def inactivity_decay_tick(self) -> None:
        """Decay the price of every user idle past the configured threshold.

        Reads the threshold from ``settings.inactivity_threshold_seconds`` and
        the decay rate from ``settings.inactivity_decay`` (default 4%).
        :func:`apply_inactivity_decay` already enforces the ``min_price``
        floor, so the floor invariant is honoured automatically.
        """
        threshold = self._settings.inactivity_threshold_seconds
        decay = self._settings.inactivity_decay
        min_price = Decimal(str(self._settings.min_price))

        # ``list_active_in_last`` returns users *within* the window; we want
        # users *outside* it. Iterate all accounts and filter on the inverse
        # condition — the active window is small relative to the guild so
        # this is the cheaper path in any sensible-sized guild and mirrors
        # the original's full sweep.
        from datetime import UTC, datetime

        now = datetime.now(tz=UTC)
        for account in await self._user_repo.list_all(self._guild_id):
            idle_seconds = (now - account.last_activity).total_seconds()
            if idle_seconds <= threshold:
                continue

            stock = await self._price_repo.get(self._guild_id, account.user_id)
            if stock is None:
                continue

            new_price = apply_inactivity_decay(stock.current, decay, min_price)
            if new_price == stock.current:
                continue
            await self._write_price(stock, new_price)

    # -- VC extra-responder periodic boost ---------------------------------

    async def vc_boost_tick(
        self,
        *,
        extra_boosts: Iterable[VcExtraBoost],
        now: datetime,
    ) -> list[VcExtraBoost]:
        """Apply the periodic boost to every still-eligible extra responder.

        Implements the original ``vc_extra_boost_step``:

        * an entry whose ``end_time`` has elapsed is dropped from the survivor
          list and no boost is applied;
        * an entry within the window but whose ``last_boost`` is younger than
          ``vc_extra_boost_interval_seconds`` is kept untouched (its cooldown
          has not yet elapsed);
        * an entry whose interval has elapsed AND whose user is still in voice
          (per :class:`VoiceSessionStore`) is boosted by
          ``settings.vc_extra_boost_multiplier`` via
          :func:`apply_floor_stall`, and its ``last_boost`` advances to
          ``now``.

        Returns the surviving :class:`VcExtraBoost` list (with refreshed
        timestamps where applicable). Storage is the caller's responsibility
        — the per-user extra-boost map is volatile in the original bot and
        Phase 9's task layer owns its lifecycle.
        """
        interval = self._settings.vc_extra_boost_interval_seconds
        multiplier = Decimal(str(self._settings.vc_extra_boost_multiplier))
        min_price = Decimal(str(self._settings.min_price))

        survivors: list[VcExtraBoost] = []
        for boost in extra_boosts:
            if now >= boost.end_time:
                continue  # window expired — drop entry, no boost

            if (now - boost.last_boost).total_seconds() < interval:
                survivors.append(boost)  # cooldown not elapsed — keep as-is
                continue

            if await self._voice_sessions.get(boost.user_id) is None:
                survivors.append(boost)  # not in voice — keep entry but no boost
                continue

            stock = await self._price_repo.get(self._guild_id, boost.user_id)
            if stock is None:
                survivors.append(boost)  # no stock to boost (silent skip)
                continue

            proposed = stock.current * multiplier
            new_price = apply_floor_stall(stock.current, proposed, min_price)
            if new_price != stock.current:
                await self._write_price(stock, new_price)
            survivors.append(replace(boost, last_boost=now))

        return survivors
