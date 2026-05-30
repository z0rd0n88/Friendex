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

  **Deliberate divergence from the original spec.** The original bot routed the
  decayed proposal through :func:`apply_floor_stall` (which attenuates drops
  near the floor); Phase 4 chose :func:`apply_inactivity_decay` here, which
  applies a *hard* ``min_price`` clamp instead. At $100 with 4% decay both
  agree ($96.00); near the floor they diverge ($71 → $70.00 here vs. ~$70.72
  with floor-stall). This is the Phase-4-pinned semantics; see the Phase 8b
  review baton for the rationale.
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
computed dynamically from price history (open-Q9 decision), not stored. Every
price-changing path here appends a :class:`PricePoint` to history under the
same lock, so the dynamic-high/low consumer (Phase 11 ``/price``, ``/trending``)
sees tick-driven moves alongside trade-driven moves. ``all_time_high`` is
ratcheted up — never lowered — whenever a tick produces a new peak.

**Guild scoping (ADR-0001).** ``guild_id`` is a constructor argument so the
single shared :class:`LockManager` Phase 14 injects across every per-guild
scope cannot serialise unrelated guilds against each other. The composite
``"<guild_id>:<user_id>"`` lock key is built by :meth:`_lock_key` and used at
every mutation site.

**Concurrency (RMW atomicity).** Each per-user price update is a
read-modify-write sequence executed *entirely inside* the lock: take the lock,
re-read the stock, compute the new price from that fresh snapshot, then
upsert + append history. A concurrent trade landing between the outer
pre-filter ``get`` and the lock acquire is therefore never clobbered — the
in-lock read sees the trade's write, and the tick recomputes from it. Locks
are non-reentrant — at most one ``locked()`` call per critical section.

**Immutability.** Stored aggregates are dataclasses but treated as immutable:
a tick mutation reads the stock, builds a replaced copy via
:func:`dataclasses.replace`, and round-trips it through ``upsert``. Inputs
are never mutated in place.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog

from friendex.application.lock_manager import guild_lock_key
from friendex.domain.models import PricePoint
from friendex.domain.price_engine import (
    apply_floor_stall,
    apply_inactivity_decay,
    compute_activity_return,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

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

# Module-level structlog logger — keyword arguments are picked up by the
# configured processor chain in ``adapters/config.py``.
_log = structlog.get_logger(__name__)


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

        Thin shim around :func:`guild_lock_key` (#82 H16). The single
        :class:`LockManager` Phase 14 wires across every per-guild service
        scope would otherwise serialise the same user across guilds —
        composing the guild id into the key guarantees per-guild isolation,
        verified by the load-bearing two-guild barrier test in Phase 8a.
        """
        return guild_lock_key(self._guild_id, user_id)

    async def _rmw_price(
        self,
        user_id: str,
        compute: Callable[[Stock], Decimal],
    ) -> None:
        """Atomically read, compute, write, and append-history for one stock.

        The whole sequence runs inside ``async with self._locks.locked(...)``
        so a concurrent trade landing on the same ``(guild, user)`` key sees a
        consistent before-state and is never clobbered by a tick that read
        the stock before the lock was held. ``compute`` is the domain-pure
        delta function (it must not perform I/O or mutate inputs); a return
        equal to ``stock.current`` is treated as a no-op and skips the write.

        On every actual price change the helper:

        * upserts a replaced :class:`Stock` with the new ``current`` and a
          ratcheted ``all_time_high`` (never lowered);
        * appends a :class:`PricePoint` to history (Phase-3a correction 4 —
          24h high/low is computed dynamically from history).
        """
        async with self._locks.locked(self._lock_key(user_id)):
            stock = await self._price_repo.get(self._guild_id, user_id)
            if stock is None:
                return
            new_price = compute(stock)
            if new_price == stock.current:
                return
            now = datetime.now(tz=UTC)
            new_ath = max(stock.all_time_high, new_price)
            await self._price_repo.upsert(
                self._guild_id,
                replace(stock, current=new_price, all_time_high=new_ath),
            )
            await self._price_repo.append_history(
                self._guild_id,
                user_id,
                PricePoint(price=new_price, timestamp=now),
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
        min_price = Decimal(str(self._settings.min_price))
        activity_k = self._settings.activity_tick_k

        for account in await self._user_repo.list_all(self._guild_id):
            stock = await self._price_repo.get(self._guild_id, account.user_id)
            if stock is None:
                continue  # cheap pre-filter — RMW re-checks under lock

            # Pre-compute the return delta now — it depends only on the account's
            # *today* bucket (which the tick does not mutate), not on the stock
            # snapshot. ``ret_pct`` is a per-iteration loop-local, so the closure
            # captures it via a default argument to avoid the late-binding trap
            # (ruff B023). ``min_price`` is a loop-invariant defined before the
            # for-loop, so the same defensive capture is applied below for
            # consistency with :meth:`inactivity_decay_tick` (#82 L2 dead-code
            # sweep: both ticks now use default-arg capture for every closed-over
            # constant, removing the implicit asymmetry the original code had).
            ret_pct = compute_activity_return(account.today, activity_k)

            def compute(
                stock_now: Stock,
                _ret_pct: Decimal = ret_pct,
                _min_price: Decimal = min_price,
            ) -> Decimal:
                proposed = stock_now.current * (_ONE + _ret_pct / _PERCENT)
                return apply_floor_stall(stock_now.current, proposed, _min_price)

            await self._rmw_price(account.user_id, compute)

    # -- inactivity decay tick ---------------------------------------------

    async def inactivity_decay_tick(self) -> None:
        """Decay the price of every user idle past the configured threshold.

        Reads the threshold from ``settings.inactivity_threshold_seconds`` and
        the decay rate from ``settings.inactivity_decay`` (default 4%).
        :func:`apply_inactivity_decay` already enforces the ``min_price``
        floor, so the floor invariant is honoured automatically. See the
        module docstring for the deliberate divergence from the original
        spec's floor-stall behaviour near the floor.
        """
        threshold = self._settings.inactivity_threshold_seconds
        decay = self._settings.inactivity_decay
        min_price = Decimal(str(self._settings.min_price))

        # ``list_active_in_last`` returns users *within* the window; we want
        # users *outside* it. Iterate all accounts and filter on the inverse
        # condition — the active window is small relative to the guild so
        # this is the cheaper path in any sensible-sized guild and mirrors
        # the original's full sweep.
        now = datetime.now(tz=UTC)
        for account in await self._user_repo.list_all(self._guild_id):
            idle_seconds = (now - account.last_activity).total_seconds()
            if idle_seconds <= threshold:
                continue

            stock = await self._price_repo.get(self._guild_id, account.user_id)
            if stock is None:
                continue  # cheap pre-filter — RMW re-checks under lock

            # Default-arg capture for ``decay`` and ``min_price`` mirrors the
            # activity tick's ``compute`` above so both closures use the same
            # defensive style (#82 L2). Neither value is a loop-local here, so
            # the capture is purely stylistic, but pinning the symmetry makes
            # the two tick functions read as obviously analogous.
            def compute(
                stock_now: Stock,
                _decay: float = decay,
                _min_price: Decimal = min_price,
            ) -> Decimal:
                return apply_inactivity_decay(stock_now.current, _decay, _min_price)

            await self._rmw_price(account.user_id, compute)

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
                # Issue #84 M (silent-failures branch): the pre-fix code
                # appended the boost to the survivor list, causing the silent
                # skip to recur on every subsequent tick. Drop the entry so
                # the cycle terminates AND log a structured warning so the
                # operator catches the persistence drift.
                _log.warning(
                    "vc_boost_no_stock",
                    user_id=boost.user_id,
                    guild_id=self._guild_id,
                )
                continue

            def compute(stock_now: Stock) -> Decimal:
                proposed = stock_now.current * multiplier
                return apply_floor_stall(stock_now.current, proposed, min_price)

            await self._rmw_price(boost.user_id, compute)
            survivors.append(replace(boost, last_boost=now))

        return survivors
